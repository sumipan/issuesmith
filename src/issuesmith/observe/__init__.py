"""issuesmith.observe -- pipeline observation layer.

observe() reads queue state, exec records, labels, and metrics to produce
a list of typed events. Its only side effect is the api_brake notification flag
(see github_api_status).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    OrphanExecEvent,
    SystemicStepFailureEvent,
    TaskTimeoutEvent,
)

if TYPE_CHECKING:
    from ghdag.forge import ForgePort

    from issuesmith.config import IssuesmithConfig
    from issuesmith.queue_store import QueueSnapshot

logger = logging.getLogger(__name__)

_ISSUE_FIELDS = ["labels", "number", "state"]


@dataclass
class ObserveSnapshot:
    """Per-tick forge read cache shared by the API-backed detectors (#3768).

    Each issue is fetched at most once per tick; every forge call is charged against
    ``max_api_calls``. Once the budget is spent, further calls are skipped with one WARN.
    """

    issues: dict[int, dict] = field(default_factory=dict)
    max_api_calls: int = 0
    api_calls: int = 0
    _attempted: set[int] = field(default_factory=set)
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


def _observed_issues(snapshot: "QueueSnapshot") -> list[int]:
    """in_flight issues first (sorted), then queued-only issues (sorted)."""
    in_flight = {
        entry["issue"]
        for entry in snapshot.in_flight
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    }
    queued = {req.issue for req in snapshot.active_requests} - in_flight
    return sorted(in_flight) + sorted(queued)


def _prefetch(
    snapshot: "QueueSnapshot", client: "ForgePort", max_api_calls: int,
) -> ObserveSnapshot:
    """Fetch every observed issue once, leaving one call for the dag_terminated list_issues."""
    obs_snapshot = ObserveSnapshot(max_api_calls=max_api_calls)
    for issue_num in _observed_issues(snapshot):
        obs_snapshot.get_issue(client, issue_num, reserve=1)
    return obs_snapshot


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

    events.extend(_detect_stall(snapshot, config, now, obs.stall_minutes))
    events.extend(_detect_task_timeout(snapshot, now, obs.task_timeout_minutes))
    events.extend(_detect_orphan_exec(snapshot, config, dag_states))
    obs_snapshot = _prefetch(snapshot, client, obs.max_api_calls)
    events.extend(_detect_dag_terminated(snapshot, client, config, obs_snapshot, dag_states))
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
            phase = phase_by_issue.get(issue, _role_to_phase(entry.get("role", ""), config))
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


def _detect_orphan_exec(
    snapshot: "QueueSnapshot",
    config: "IssuesmithConfig",
    dag_states: "dict | None" = None,
) -> list[ObserveEvent]:
    from issuesmith.observe.dag_state import DagState

    exec_path = config.paths.exec_jsonl
    done_dir = config.paths.done_dir
    if not exec_path.exists():
        return []

    in_flight_issues = {
        entry.get("issue")
        for entry in snapshot.in_flight
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
    }

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
        if not idempotency_key.startswith("issuesmith:"):
            continue
        uuid_val = row.get("uuid")
        if not isinstance(uuid_val, str) or not uuid_val:
            continue
        if (done_dir / uuid_val).exists():
            continue
        issue = _issue_from_key(idempotency_key)
        if issue not in in_flight_issues:
            # If the DAG is still running, do not flag this as an orphan.
            if dag_states is not None and issue is not None:
                state = dag_states.get(issue)
                if isinstance(state, DagState) and state.status == "running":
                    continue
            events.append(OrphanExecEvent(uuid=uuid_val, issue=issue))

    return events


def _issue_from_key(key: str) -> int | None:
    """Return the issue number from ``<workflow>:<handler>:<issue>[:<generation>]``.

    Recovery / redispatch append a generation suffix (``issuesmith:impl:3548:1``); the issue
    number is always the third segment, never the last one (sumipan/nexus#3523).
    """
    parts = key.split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return None


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
        role_engines = list(role_cfg.allowed)
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
    if getattr(snapshot, "halt_event", None) == "main_red":
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
