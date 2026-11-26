"""issuesmith.observe -- pipeline observation layer.

observe() reads queue state, exec records, labels, and metrics to produce
a list of typed events. Its only side effect is the api_brake notification flag
(see github_api_status).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from issuesmith.observe.dag_state import load_dag_states
from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    ChainHaltedEvent,
    DagTerminatedEvent,
    GitHubApiLowEvent,
    GitHubApiRecoveredEvent,
    IssueStallEvent,
    LabelDriftEvent,
    MainGreenEvent,
    MainRedEvent,
    ObserveEvent,
    ObserveFailureWatchSeedEvent,
    ObserveLedgerPatchEvent,
    OrphanExecEvent,
    SystemicStepFailureEvent,
    TaskTimeoutEvent,
    UnstartedTaskEvent,
)

if TYPE_CHECKING:
    from ghdag.forge import ForgePort

    from issuesmith.config import IssuesmithConfig
    from issuesmith.queue_store import QueueSnapshot

logger = logging.getLogger(__name__)

_ISSUESMITH_IDEMPOTENCY_PREFIX = "issuesmith:"
_ISSUE_FIELDS = ["labels", "number", "state", "state_reason", "closed_at"]


@dataclass
class ObserveSnapshot:
    """Per-tick forge read cache shared by the API-backed detectors (#3768).

    Each issue is fetched at most once per tick; every forge call is charged against
    ``max_api_calls``. Once the budget is spent, further calls are skipped with one WARN.
    """

    issues: dict[int, dict] = field(default_factory=dict)
    comments: dict[int, list[dict]] = field(default_factory=dict)
    max_api_calls: int = 0
    api_calls: int = 0
    _attempted: set[int] = field(default_factory=set)
    _comments_attempted: set[int] = field(default_factory=set)
    _warned: bool = False

    def take_api_call(self, *, reserve: int = 0) -> bool:
        """Charge one forge call; ``reserve`` keeps that many calls for later callers."""
        if self.api_calls + reserve >= self.max_api_calls:
            if not self._warned:
                logger.warning(
                    "observe: max_api_calls=%d reached; skipping remaining forge reads",
                    self.max_api_calls,
                )
                self._warned = True
            return False
        self.api_calls += 1
        return True

    def get_issue(
        self, client: "ForgePort", issue_num: int, *, reserve: int = 0,
    ) -> dict | None:
        """Return the cached issue, fetching it once if the budget allows."""
        if issue_num not in self._attempted:
            if not self.take_api_call(reserve=reserve):
                return None
            self._attempted.add(issue_num)
            try:
                self.issues[issue_num] = client.issue_get(issue_num, fields=_ISSUE_FIELDS)
            except Exception:
                pass
        return self.issues.get(issue_num)

    def get_issue_comments(
        self, client: "ForgePort", issue_num: int, *, reserve: int = 0,
    ) -> list[dict] | None:
        if issue_num not in self._comments_attempted:
            if not self.take_api_call(reserve=reserve):
                return None
            self._comments_attempted.add(issue_num)
            try:
                self.comments[issue_num] = client.get_issue_comments(issue_num)
            except Exception:
                logger.warning(
                    "observe: failed to read comments for issue #%s",
                    issue_num,
                    exc_info=True,
                )
        return self.comments.get(issue_num)


def _observed_issues(snapshot: "QueueSnapshot") -> list[int]:
    """in_flight issues first (sorted), then queued-only issues (sorted)."""
    in_flight = {
        entry["issue"]
        for entry in snapshot.in_flight
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    }
    queued = {req.issue for req in snapshot.active_requests} - in_flight
    return sorted(in_flight) + sorted(queued)


def _prefetch_into(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    obs_snapshot: ObserveSnapshot,
) -> None:
    """Fetch every observed issue once into an existing cache."""
    for issue_num in _observed_issues(snapshot):
        obs_snapshot.get_issue(client, issue_num, reserve=1)


def _as_obs_snapshot(obs_snapshot: "ObserveSnapshot | int") -> ObserveSnapshot:
    """Accept a bare ``max_api_calls`` budget (direct detector calls) as an empty cache."""
    if isinstance(obs_snapshot, ObserveSnapshot):
        return obs_snapshot
    return ObserveSnapshot(max_api_calls=obs_snapshot)


def observe(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    *,
    metrics_path: Path | None = None,
    now: datetime | None = None,
    github_api_low: bool | None = None,
) -> list[ObserveEvent]:
    """Collect pipeline events.

    ``github_api_low=True`` runs the reduced mode: no forge calls, only ``dag_terminated``
    (in_flight candidates, local DAG state), ``orphan_exec`` (#3769) and the local
    ``main_red`` / ``main_green`` state file (#3664). ``None`` (default)
    derives it from audit.jsonl via :func:`github_api_status` when ``api_brake`` is enabled
    — the only side effect of observe(), persisting the low/recovered notification flag.
    With ``api_brake`` disabled, ``None`` behaves as ``False`` and observe() is read-only.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    transition_events: list[ObserveEvent] = []
    if github_api_low is None:
        github_api_low, transition_events = github_api_status(config, now)

    dag_states = load_dag_states(
        config.paths.exec_jsonl,
        config.paths.done_dir,
        config.paths.done_dir.parent / "running",
    )

    if github_api_low:
        reduced: list[ObserveEvent] = []
        reduced.extend(_detect_dag_terminated_local(snapshot, config, dag_states))
        reduced.extend(_detect_orphan_exec(snapshot, config, dag_states))
        reduced.extend(_detect_main_health(snapshot, config))
        reduced.extend(transition_events)
        return reduced

    obs = config.observe
    events: list[ObserveEvent] = []

    events.extend(
        _detect_stall(snapshot, config, now, obs.stall_minutes),
    )
    events.extend(_detect_task_timeout(snapshot, now, obs.task_timeout_minutes))
    events.extend(_detect_orphan_exec(snapshot, config, dag_states))
    obs_snapshot = ObserveSnapshot(max_api_calls=obs.max_api_calls)
    events.extend(
        _detect_failure_marks(snapshot, client, config, obs_snapshot, now),
    )
    events.extend(
        _detect_unstarted_tasks(snapshot, client, config, obs_snapshot, now),
    )
    _prefetch_into(snapshot, client, obs_snapshot)
    events.extend(_detect_dag_terminated_sync(snapshot, config, dag_states))
    events.extend(_detect_label_drift(snapshot, client, config, obs_snapshot))
    events.extend(_detect_chain_halted(snapshot))
    events.extend(_detect_systemic_failure(
        snapshot,
        config,
        metrics_path=metrics_path,
        window_minutes=obs.systemic_window_minutes,
        min_issues=obs.systemic_min_issues,
        now=now,
    ))
    events.extend(_detect_all_engines_paused(config))
    events.extend(_detect_main_health(snapshot, config))
    events.extend(transition_events)

    return events


def github_api_status(
    config: "IssuesmithConfig", now: datetime,
) -> tuple[bool, list[ObserveEvent]]:
    """Return ``(github_api_low, transition_events)`` for the api_brake (#3769).

    ``GitHubApiLowEvent`` / ``GitHubApiRecoveredEvent`` are emitted only on a state change;
    the last notified state is kept in quota-gate.json ``resources.github_api_notified`` so
    consecutive ticks do not repeat them. Disabled brake → ``(False, [])`` without I/O.
    """
    from issuesmith.config import ApiBreakConfig
    from issuesmith.quota_gate import (
        is_github_api_low,
        read_github_api_state,
        write_github_api_notified,
    )

    brake = getattr(config, "api_brake", None) or ApiBreakConfig()
    if not brake.enabled:
        return False, []

    state = read_github_api_state(config.paths.exec_jsonl.parent / "audit.jsonl")
    low = is_github_api_low(state, brake.min_remaining, now)
    try:
        changed = write_github_api_notified(config.paths.quota_state, low)
    except (OSError, ValueError):
        logger.warning("observe: failed to persist github_api_notified", exc_info=True)
        return low, []
    if not changed:
        return low, []
    if low and state is not None:
        return low, [GitHubApiLowEvent(remaining=state.remaining)]
    return low, [GitHubApiRecoveredEvent()]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def _detect_stall(
    snapshot: "QueueSnapshot",
    config: "IssuesmithConfig",
    now: datetime,
    stall_minutes: int,
) -> list[ObserveEvent]:
    events: list[ObserveEvent] = []
    phase_by_issue = _active_phase_by_issue(snapshot)

    for entry in snapshot.in_flight:
        if not isinstance(entry, dict):
            continue
        issue = entry.get("issue")
        if not isinstance(issue, int):
            continue
        dispatched_raw = entry.get("dispatched_at")
        if not dispatched_raw:
            continue
        try:
            dispatched = datetime.fromisoformat(str(dispatched_raw))
        except ValueError:
            continue
        if dispatched.tzinfo is None:
            dispatched = dispatched.replace(tzinfo=timezone.utc)
        elapsed_min = int((now.timestamp() - dispatched.timestamp()) / 60)
        if elapsed_min >= stall_minutes:
            if issue in phase_by_issue:
                phase = phase_by_issue[issue]
            else:
                entry_phase = entry.get("phase")
                if isinstance(entry_phase, str) and entry_phase:
                    phase = entry_phase
                else:
                    phase = _role_to_phase(entry.get("role", ""), config)
            events.append(IssueStallEvent(issue=issue, phase=phase, minutes=elapsed_min))

    return events


def _detect_task_timeout(
    snapshot: "QueueSnapshot",
    now: datetime,
    task_timeout_minutes: int,
) -> list[ObserveEvent]:
    events: list[ObserveEvent] = []
    for entry in snapshot.in_flight:
        if not isinstance(entry, dict):
            continue
        uuid_val = str(entry.get("uuid") or "")
        dispatched_raw = entry.get("dispatched_at")
        if not dispatched_raw or not uuid_val:
            continue
        try:
            dispatched = datetime.fromisoformat(str(dispatched_raw))
        except ValueError:
            continue
        if dispatched.tzinfo is None:
            dispatched = dispatched.replace(tzinfo=timezone.utc)
        elapsed_min = int((now.timestamp() - dispatched.timestamp()) / 60)
        if elapsed_min >= task_timeout_minutes:
            events.append(TaskTimeoutEvent(uuid=uuid_val, elapsed=elapsed_min))
    return events


def _workflow_key_prefixes(config: "IssuesmithConfig") -> set[str]:
    prefixes = {config.label_namespace}
    for wf in config.observe.workflows:
        prefixes.add(wf.name)
    return prefixes


def _workflow_name_from_key(idempotency_key: str, prefixes: set[str]) -> str | None:
    if idempotency_key.startswith(_ISSUESMITH_IDEMPOTENCY_PREFIX):
        head = _ISSUESMITH_IDEMPOTENCY_PREFIX.rstrip(":")
    else:
        head = idempotency_key.split(":", 1)[0]
    return head if head in prefixes else None


def _detect_orphan_exec(
    snapshot: "QueueSnapshot",
    config: "IssuesmithConfig",
    dag_states: "dict | None" = None,
) -> list[ObserveEvent]:
    from issuesmith.observe.dag_state import DagState, load_dag_states

    exec_path = config.paths.exec_jsonl
    done_dir = config.paths.done_dir
    running_dir = config.paths.done_dir.parent / "running"
    if not exec_path.exists():
        return []

    in_flight_issues = {
        entry.get("issue")
        for entry in snapshot.in_flight
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    }

    prefixes = _workflow_key_prefixes(config)
    external_dag_states: dict[str, dict[int, DagState]] = {}

    events: list[ObserveEvent] = []
    for raw in exec_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        idempotency_key = str(row.get("idempotency_key", ""))
        workflow_name = _workflow_name_from_key(idempotency_key, prefixes)
        if workflow_name is None:
            continue
        uuid_val = row.get("uuid")
        if not isinstance(uuid_val, str) or not uuid_val:
            continue
        if (done_dir / uuid_val).exists():
            continue
        issue = _issue_from_key(idempotency_key)
        if workflow_name == config.label_namespace:
            if issue in in_flight_issues:
                continue
            if dag_states is not None and issue is not None:
                state = dag_states.get(issue)
                if isinstance(state, DagState) and state.status == "running":
                    continue
        else:
            if workflow_name not in external_dag_states:
                external_dag_states[workflow_name] = load_dag_states(
                    exec_path,
                    done_dir,
                    running_dir,
                    workflow=workflow_name,
                )
            ext_states = external_dag_states[workflow_name]
            if issue is not None:
                state = ext_states.get(issue)
                if isinstance(state, DagState) and state.status == "running":
                    continue
        events.append(OrphanExecEvent(uuid=uuid_val, issue=issue))

    return events


def _issue_from_key(key: str) -> int | None:
    """Return the issue number from ``<workflow>:<handler>:<issue>[:<generation>]``.

    Recovery / redispatch append a generation suffix (``issuesmith:impl:3548:1``); the issue
    number is always the third segment, never the last one (generation suffixes
    add a fourth segment).
    """
    parts = key.split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return None


def _done_dir_paths(config: "IssuesmithConfig") -> tuple[Path, Path, Path]:
    done_dir = config.paths.done_dir
    parent = done_dir.parent
    return done_dir, parent / "running", parent / "quota-waiting"


def _marker_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _done_first_line(done_dir: Path, uuid: str) -> str:
    path = done_dir / uuid
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text:
        return ""
    return text.splitlines()[0].strip()


def _phase_from_labels(labels: set[str], ns: str, config: "IssuesmithConfig") -> str:
    for p in config.phases:
        if f"{ns}:{p.name}-running" in labels:
            return p.name
    for lbl in labels:
        if lbl.startswith(f"{ns}:") and lbl.endswith("-running"):
            return lbl[len(ns) + 1 : -len("-running")]
    return ""


def _namespace_for_key(key: str, config: "IssuesmithConfig") -> str:
    head = key.split(":", 1)[0]
    for wf in config.observe.workflows:
        if head == wf.name:
            return wf.label_namespace
    if head == config.label_namespace or key.startswith("issuesmith:"):
        return ""
    return head


def _collect_failed_by_namespace(
    config: "IssuesmithConfig",
):
    from issuesmith.observe.dag_state import DagState, load_dag_states

    done_dir, running_dir, _ = _done_dir_paths(config)
    exec_path = config.paths.exec_jsonl
    rows: list[tuple[str, int, DagState]] = []
    main = load_dag_states(exec_path, done_dir, running_dir)
    for issue, state in main.items():
        if isinstance(state, DagState) and state.status == "failed":
            rows.append((config.label_namespace, issue, state))
    for wf in config.observe.workflows:
        wf_states = load_dag_states(
            exec_path, done_dir, running_dir, workflow=wf.name,
        )
        for issue, state in wf_states.items():
            if isinstance(state, DagState) and state.status == "failed":
                rows.append((wf.label_namespace, issue, state))
    return rows


def _known_exec_uuids(config: "IssuesmithConfig") -> set[str]:
    exec_path = config.paths.exec_jsonl
    if not exec_path.exists():
        return set()
    prefixes = {config.label_namespace}
    for wf in config.observe.workflows:
        prefixes.add(wf.name)
    uuids: set[str] = set()
    for raw in exec_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        key = str(row.get("idempotency_key", ""))
        if _workflow_name_from_key(key, prefixes) is None:
            continue
        uuid = row.get("uuid")
        if isinstance(uuid, str) and uuid:
            uuids.add(uuid)
    return uuids


def _parse_issue_time(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pipeline_andon_near_failure(
    comments: list[dict],
    failed_at: datetime,
    timeout_minutes: int,
) -> bool:
    from issuesmith.andon import from_comment

    window_start = failed_at - timedelta(minutes=timeout_minutes)
    window_end = failed_at + timedelta(minutes=5)
    for comment in comments:
        body = str(comment.get("body") or "")
        parsed = from_comment(body)
        if parsed is None or parsed.step == "observe":
            continue
        created = _parse_issue_time(comment.get("created_at"))
        if created is None:
            continue
        if window_start <= created <= window_end:
            return True
    return False


def _closed_reason(issue_data: dict | None) -> str | None:
    if issue_data is None:
        return None
    if str(issue_data.get("state", "")).upper() != "CLOSED":
        return None
    reason = issue_data.get("state_reason")
    if reason is None or reason == "":
        return "completed"
    return str(reason).casefold()


def _detect_failure_marks(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    obs_snapshot: ObserveSnapshot,
    now: datetime,
) -> list[ObserveEvent]:
    from issuesmith.observe.dag_state import DagState

    done_dir, _, _ = _done_dir_paths(config)
    watch_since = snapshot.observe_failure_watch_since
    seen = set(snapshot.observe_failure_seen)
    reopened = set(snapshot.observe_reopened)
    failed_rows = _collect_failed_by_namespace(config)

    if watch_since is None:
        uuids = tuple(
            st.failed_uuid
            for _ns, _issue, st in failed_rows
            if st.failed_uuid
        )
        return [
            ObserveFailureWatchSeedEvent(
                watch_since=now.isoformat(),
                failed_uuids=uuids,
            )
        ]

    try:
        since_dt = datetime.fromisoformat(watch_since)
    except ValueError:
        since_dt = now
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)

    stale = seen - _known_exec_uuids(config)
    events: list[ObserveEvent] = []
    if stale:
        events.append(ObserveLedgerPatchEvent(seen_remove=tuple(sorted(stale))))

    for _wf_ns, issue, st in sorted(failed_rows, key=lambda r: (r[1], r[0])):
        if not isinstance(st, DagState) or not st.failed_uuid:
            continue
        marker = done_dir / st.failed_uuid
        mtime = _marker_mtime(marker)
        if st.failed_uuid in seen:
            continue
        if mtime is None or mtime < since_dt:
            continue

        issue_data = obs_snapshot.get_issue(client, issue, reserve=1)
        if issue_data is None:
            continue
        comments = obs_snapshot.get_issue_comments(client, issue)
        if comments is None:
            continue

        failed_at = mtime.isoformat()
        result_line = _done_first_line(done_dir, st.failed_uuid)
        state_upper = str(issue_data.get("state", "OPEN")).upper()
        state_reason = issue_data.get("state_reason")
        closed_at = issue_data.get("closed_at")
        if not closed_at:
            closed_at = "unknown"

        labels = {
            lbl["name"] if isinstance(lbl, dict) else str(lbl)
            for lbl in issue_data.get("labels", [])
        }
        phase = _phase_from_labels(labels, config.label_namespace, config)
        namespace = _namespace_for_key(st.key, config)

        event = DagTerminatedEvent(
            issue=issue,
            key=st.key,
            phase=phase,
            failed_step=st.failed_step,
            failed_uuid=st.failed_uuid,
            result_path=st.failed_result_path,
            namespace=namespace,
            result_first_line=result_line,
            failed_at=failed_at,
            issue_state=state_upper,
            state_reason=str(state_reason) if state_reason is not None else None,
            closed_at=str(closed_at) if closed_at else None,
            mark_seen=True,
        )

        if _pipeline_andon_near_failure(
            comments, mtime, config.observe.task_timeout_minutes,
        ):
            event = dataclass_replace(
                event,
                suppress_andon=True,
            )
            events.append(event)
            continue

        if state_upper == "CLOSED":
            reason = _closed_reason(issue_data)
            guard_key = f"{issue}:{st.failed_step}"
            if reason == "not_planned":
                events.append(dataclass_replace(event, not_planned=True))
                continue
            if guard_key in reopened:
                events.append(dataclass_replace(event, reopen_guard=True))
                continue
            if reason in (None, "completed"):
                events.append(dataclass_replace(event, needs_reopen=True))
                continue

        events.append(event)

    return events


def dataclass_replace(obj: DagTerminatedEvent, **changes: Any) -> DagTerminatedEvent:
  from dataclasses import replace
  return replace(obj, **changes)


def _detect_dag_terminated_sync(
    snapshot: "QueueSnapshot",
    config: "IssuesmithConfig",
    dag_states: dict,
) -> list[ObserveEvent]:
    seen = set(snapshot.observe_failure_seen)
    if not seen:
        return []
    events: list[ObserveEvent] = []
    for _wf_ns, issue, st in _collect_failed_by_namespace(config):
        if st.failed_uuid not in seen or st.status != "failed":
            continue
        namespace = _namespace_for_key(st.key, config)
        events.append(
            DagTerminatedEvent(
                issue=issue,
                key=st.key,
                phase="",
                failed_step=st.failed_step,
                failed_uuid=st.failed_uuid,
                result_path=st.failed_result_path,
                namespace=namespace,
                sync_only=True,
            )
        )
    return events


def _row_step(row: dict) -> str:
    ann = row.get("annotations") or {}
    if isinstance(ann, dict) and ann.get("step_name"):
        return str(ann["step_name"])
    return str(row.get("uuid", ""))


def _detect_unstarted_tasks(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    obs_snapshot: ObserveSnapshot,
    now: datetime,
) -> list[ObserveEvent]:
    from ghdag.io import done as ghdag_done

    if snapshot.observe_failure_watch_since is None:
        return []

    done_dir, running_dir, quota_dir = _done_dir_paths(config)
    exec_path = config.paths.exec_jsonl
    if not exec_path.exists():
        stale = tuple(uuid for uuid, _ts in snapshot.observe_unstarted_first_seen)
        if not stale:
            return []
        return [ObserveLedgerPatchEvent(unstarted_remove=stale)]
    prefixes = {config.label_namespace}
    for wf in config.observe.workflows:
        prefixes.add(wf.name)

    dag_rows: dict[tuple[str, int], list[dict]] = {}
    dag_last_key: dict[tuple[str, int], str] = {}
    for raw in exec_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        key = str(row.get("idempotency_key", ""))
        workflow = _workflow_name_from_key(key, prefixes)
        if workflow is None:
            continue
        issue = _issue_from_key(key)
        if issue is None:
            continue
        dag = (workflow, issue)
        dag_last_key[dag] = key
        dag_rows.setdefault(dag, []).append(row)

    read_done_content = ghdag_done.read_done_content
    interpret_done = ghdag_done.interpret_done
    first_seen_map = dict(snapshot.observe_unstarted_first_seen)
    ledger_sets: list[tuple[str, str]] = []
    ledger_remove: list[str] = []
    events: list[ObserveEvent] = []
    timeout = config.observe.task_timeout_minutes

    latest_uuids = {
        str(row.get("uuid", ""))
        for dag, rows in dag_rows.items()
        for row in rows
        if row.get("idempotency_key") == dag_last_key[dag]
        and str(row.get("uuid", ""))
    }
    ledger_remove.extend(sorted(set(first_seen_map) - latest_uuids))

    for dag in sorted(dag_rows):
        _workflow, issue = dag
        current_key = dag_last_key[dag]
        rows = [r for r in dag_rows[dag] if r.get("idempotency_key") == current_key]
        for row in rows:
            uuid = str(row.get("uuid", ""))
            if not uuid:
                continue
            has_done = (done_dir / uuid).exists()
            has_running = (running_dir / f"{uuid}.json").exists()
            has_quota = (quota_dir / f"{uuid}.json").exists()
            depends = row.get("depends") or []
            deps_ok = True
            if isinstance(depends, list):
                for dep in depends:
                    raw_dep = read_done_content(done_dir, str(dep))
                    if interpret_done(raw_dep) != "success":
                        deps_ok = False
                        break
            if has_done or has_running or has_quota or not deps_ok:
                if uuid in first_seen_map:
                    ledger_remove.append(uuid)
                continue
            if uuid not in first_seen_map:
                ledger_sets.append((uuid, now.isoformat()))
                first_seen_map[uuid] = now.isoformat()
                continue
            try:
                first_dt = datetime.fromisoformat(first_seen_map[uuid])
            except ValueError:
                continue
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            elapsed = (now - first_dt).total_seconds() / 60
            if elapsed < timeout:
                continue
            namespace = _namespace_for_key(current_key, config)
            issue_data = obs_snapshot.get_issue(client, issue, reserve=1)
            if issue_data is None:
                continue
            state_upper = str(issue_data.get("state", "OPEN")).upper()
            closed_at = issue_data.get("closed_at") or "unknown"
            step = _row_step(row)
            ev = UnstartedTaskEvent(
                issue=issue,
                key=current_key,
                step=step,
                uuid=uuid,
                first_seen=first_seen_map[uuid],
                timeout_at=now.isoformat(),
                namespace=namespace,
                issue_state=state_upper,
                state_reason=issue_data.get("state_reason"),
                closed_at=str(closed_at),
            )
            if state_upper == "CLOSED":
                reason = _closed_reason(issue_data)
                guard_key = f"{issue}:{step}"
                if reason == "not_planned":
                    ledger_remove.append(uuid)
                    continue
                if guard_key in snapshot.observe_reopened:
                    ev = dataclass_replace_unstarted(ev, reopen_guard=True)
                elif reason in (None, "completed"):
                    ev = dataclass_replace_unstarted(ev, needs_reopen=True)
            events.append(ev)

    if ledger_sets or ledger_remove:
        events.insert(
            0,
            ObserveLedgerPatchEvent(
                unstarted_set=tuple(ledger_sets),
                unstarted_remove=tuple(ledger_remove),
            ),
        )
    return events


def dataclass_replace_unstarted(obj: UnstartedTaskEvent, **changes: Any) -> UnstartedTaskEvent:
    from dataclasses import replace
    return replace(obj, **changes)


def _detect_dag_terminated(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    obs_snapshot: "ObserveSnapshot | int",
    dag_states: "dict",
) -> list[ObserveEvent]:
    from issuesmith.observe.dag_state import DagState

    obs_snapshot = _as_obs_snapshot(obs_snapshot)
    ns = config.label_namespace

    # Candidate issues: in_flight union impl-phase running open issues (same set as _find_untracked_running)
    from issuesmith.queue_triage import RUNNING_LABEL

    candidates: set[int] = {
        entry["issue"]
        for entry in snapshot.in_flight
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    }
    # list_issues and issue_get share the max_api_calls budget (obs_snapshot).
    try:
        running_issues = (
            client.list_issues(RUNNING_LABEL["develop"], state="open")
            if obs_snapshot.take_api_call()
            else []
        )
        if isinstance(running_issues, list):
            for issue in running_issues:
                if isinstance(issue, dict):
                    num = issue.get("number")
                    if isinstance(num, int):
                        candidates.add(num)
    except Exception:
        pass

    events: list[ObserveEvent] = []

    for issue_num in sorted(candidates):
        state = dag_states.get(issue_num)
        if not isinstance(state, DagState) or state.status != "failed":
            continue
        issue_data = obs_snapshot.get_issue(client, issue_num)
        if issue_data is None:
            continue

        current_labels = {
            lbl["name"] if isinstance(lbl, dict) else str(lbl)
            for lbl in issue_data.get("labels", [])
        }
        phase = ""
        for p in config.phases:
            running_label = f"{ns}:{p.name}-running"
            if running_label in current_labels:
                phase = p.name
                break

        events.append(DagTerminatedEvent(
            issue=issue_num,
            key=state.key,
            phase=phase,
            failed_step=state.failed_step,
            failed_uuid=state.failed_uuid,
            result_path=state.failed_result_path,
        ))

    return events


def _phase_from_running_labels(labels: set[str], ns: str) -> str:
    prefix = f"{ns}:"
    for lbl in labels:
        if lbl == f"{ns}:running":
            return ""
        if lbl.startswith(prefix) and lbl.endswith("-running"):
            return lbl[len(prefix): -len("-running")]
    return ""


def _has_running_label(labels: set[str], ns: str) -> bool:
    if f"{ns}:running" in labels:
        return True
    prefix = f"{ns}:"
    return any(
        lbl.startswith(prefix) and lbl.endswith("-running") for lbl in labels
    )


def _detect_dag_terminated_workflows(
    client: "ForgePort",
    config: "IssuesmithConfig",
    obs_snapshot: "ObserveSnapshot | int",
) -> list[ObserveEvent]:
    from issuesmith.observe.dag_state import DagState, load_dag_states

    if not config.observe.workflows:
        return []

    obs_snapshot = _as_obs_snapshot(obs_snapshot)
    running_dir = config.paths.done_dir.parent / "running"
    events: list[ObserveEvent] = []

    for wf in config.observe.workflows:
        wf_states = load_dag_states(
            config.paths.exec_jsonl,
            config.paths.done_dir,
            running_dir,
            workflow=wf.name,
        )
        for issue_num in sorted(wf_states):
            state = wf_states.get(issue_num)
            if not isinstance(state, DagState) or state.status != "failed":
                continue
            issue_data = obs_snapshot.get_issue(client, issue_num)
            if issue_data is None:
                continue
            if str(issue_data.get("state", "")).upper() != "OPEN":
                continue
            current_labels = {
                lbl["name"] if isinstance(lbl, dict) else str(lbl)
                for lbl in issue_data.get("labels", [])
            }
            if not _has_running_label(current_labels, wf.label_namespace):
                continue
            phase = _phase_from_running_labels(current_labels, wf.label_namespace)
            events.append(DagTerminatedEvent(
                issue=issue_num,
                key=state.key,
                phase=phase,
                failed_step=state.failed_step,
                failed_uuid=state.failed_uuid,
                result_path=state.failed_result_path,
                namespace=wf.label_namespace,
            ))

    return events


def _detect_dag_terminated_local(
    snapshot: "QueueSnapshot",
    config: "IssuesmithConfig",
    dag_states: "dict",
) -> list[ObserveEvent]:
    """API-free ``_detect_dag_terminated`` for the github_api_low reduced mode (#3769).

    Candidates are in_flight only (no ``list_issues``); ``phase`` is empty because the
    running label is not read from the forge.
    """
    from issuesmith.observe.dag_state import DagState

    candidates = sorted({
        entry["issue"]
        for entry in snapshot.in_flight
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    })
    events: list[ObserveEvent] = []
    for issue_num in candidates:
        state = dag_states.get(issue_num)
        if not isinstance(state, DagState) or state.status != "failed":
            continue
        events.append(DagTerminatedEvent(
            issue=issue_num,
            key=state.key,
            phase="",
            failed_step=state.failed_step,
            failed_uuid=state.failed_uuid,
            result_path=state.failed_result_path,
        ))
    return events


def _detect_label_drift(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    obs_snapshot: "ObserveSnapshot | int",
) -> list[ObserveEvent]:
    from issuesmith.ops.labels import (
        ExecRecord,
        _exec_records_from_labels,
        _is_managed_label,
        _ns,
    )
    from issuesmith.ops.labels import (
        project as labels_project,
    )

    obs_snapshot = _as_obs_snapshot(obs_snapshot)
    events: list[ObserveEvent] = []
    ns = _ns()

    queued_issues = {req.issue for req in snapshot.active_requests}

    for issue_num in sorted(_observed_issues(snapshot)):
        issue = obs_snapshot.get_issue(client, issue_num)
        if issue is None:
            continue
        if str(issue.get("state", "")).upper() != "OPEN":
            continue

        current_labels = {
            lbl["name"] if isinstance(lbl, dict) else str(lbl)
            for lbl in issue.get("labels", [])
        }

        if issue_num in queued_issues:
            queue_state: str | None = "queued"
            exec_recs: list[ExecRecord] = []
        else:
            queue_state = None
            exec_recs = _exec_records_from_labels(current_labels, ns)

        desired = labels_project(
            issue_num,
            queue_state=queue_state,
            exec_records=exec_recs,
            andon_inbox=[],
        )

        managed_current = {lbl for lbl in current_labels if _is_managed_label(lbl, ns)}
        to_add = desired - managed_current
        to_remove = managed_current - desired
        if to_add or to_remove:
            events.append(LabelDriftEvent(
                issue=issue_num,
                add=tuple(sorted(to_add)),
                remove=tuple(sorted(to_remove)),
            ))

    return events


def _detect_chain_halted(snapshot: "QueueSnapshot") -> list[ObserveEvent]:
    events: list[ObserveEvent] = []
    for key, chain in snapshot.milestone_chains.items():
        if not isinstance(chain, dict):
            continue
        if chain.get("stage") == "halted":
            try:
                parent = int(key)
            except (ValueError, TypeError):
                continue
            reason = str(chain.get("halted_reason") or "")
            events.append(ChainHaltedEvent(parent=parent, reason=reason))
    return events


def _detect_systemic_failure(
    snapshot: "QueueSnapshot",
    config: "IssuesmithConfig",
    *,
    metrics_path: Path | None,
    window_minutes: int,
    min_issues: int,
    now: datetime,
) -> list[ObserveEvent]:
    path = metrics_path or config.paths.metrics
    if not path.exists():
        return []

    cutoff = now.timestamp() - window_minutes * 60
    groups: dict[tuple[str, str], set[int]] = {}
    groups_no_template: dict[tuple[str, str], bool] = {}

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if str(record.get("status", "")) != "failed":
            continue
        finished_at = record.get("finished_at")
        if not isinstance(finished_at, (int, float)):
            continue
        if finished_at < cutoff:
            continue

        tags = record.get("additional_tags") or {}
        if not isinstance(tags, dict):
            continue

        step = str(tags.get("step") or "")
        failure_class = str(tags.get("failure_class") or "")
        template = str(tags.get("template") or "")

        if not step or not failure_class:
            continue

        issue_raw = tags.get("issue")
        if issue_raw is None:
            issue_raw = record.get("correlation_id")
        try:
            issue_num = int(str(issue_raw)) if issue_raw is not None else None
        except (ValueError, TypeError):
            issue_num = None

        key = (step, failure_class)
        if key not in groups:
            groups[key] = set()
            groups_no_template[key] = True

        if issue_num is not None:
            groups[key].add(issue_num)

        if template:
            groups_no_template[key] = False

    events: list[ObserveEvent] = []
    for (step, failure_class), issues in groups.items():
        if len(issues) >= min_issues and groups_no_template.get((step, failure_class), False):
            events.append(SystemicStepFailureEvent(
                step=step,
                failure_class=failure_class,
                issues=tuple(sorted(issues)),
            ))

    return events


def _detect_all_engines_paused(config: "IssuesmithConfig") -> list[ObserveEvent]:
    try:
        from ghdag.quota import QuotaGate
    except ImportError:
        return []

    try:
        quota_gate = QuotaGate(state_path=config.paths.quota_state)
        snap = quota_gate.snapshot()
    except Exception:
        return []

    paused_roles: list[str] = []
    for role, role_cfg in config.engines.items():
        role_engines = [engine for engine, _ in role_cfg.heavy]
        if not role_engines:
            continue
        all_paused = all(
            _engine_is_paused(snap.engines.get(engine)) for engine in role_engines
        )
        if all_paused:
            paused_roles.append(role)

    if paused_roles and len(paused_roles) == len(config.engines):
        return [AllEnginesPausedEvent(roles=tuple(sorted(paused_roles)))]

    return []


def _detect_main_health(
    snapshot: "QueueSnapshot", config: "IssuesmithConfig",
) -> list[ObserveEvent]:
    """Read the ``issuesmith main-health`` state file (#3664); no forge call, no test run.

    Red is reported on every tick (execute() deduplicates the andon); green only while the
    queue is still halted by ``main_red``, so it resumes that halt exactly once.
    """
    from issuesmith.config import MainHealthConfig
    from issuesmith.observe.main_health import load_state, state_path

    if not isinstance(getattr(config.observe, "main_health", None), MainHealthConfig):
        return []

    state = load_state(state_path(config))
    if state is None:
        return []
    if state.status == "red":
        return [MainRedEvent(sha=state.sha, reason=state.reason, failing=state.failing)]
    if any(block.kind == "main_red" for block in snapshot.blocks):
        return [MainGreenEvent(sha=state.sha)]
    return []


def _engine_is_paused(engine_state: Any) -> bool:
    if engine_state is None:
        return False
    return getattr(engine_state, "status", None) == "paused"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_phase_by_issue(snapshot: "QueueSnapshot") -> dict[int, str]:
    result: dict[int, str] = {}
    for req in snapshot.active_requests:
        result[req.issue] = req.phase
    return result


def _role_to_phase(role: str, config: "IssuesmithConfig") -> str:
    for phase in config.phases:
        if phase.role == role:
            return phase.name
    return role
