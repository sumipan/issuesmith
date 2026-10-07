"""issuesmith queue CLI — enqueue, triage, dispatch, migrate."""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from ghdag.core.exceptions import GitHubApiError
from ghdag.forge import ForgePort, get_forge
from ghdag.quota import QuotaGate

from issuesmith.config import get_config
from issuesmith.dep_extractor import check_dependencies
from issuesmith.forge_api import api_request
from issuesmith.gate_rules.scope_coupling import (
    deletion_references_for_body,
    format_deletion_references,
)
from issuesmith.gates.dep import dependents_of, on_dep_merge_done
from issuesmith.milestone import advance_milestone_chains, milestone_last_issue_terminal_ok
from issuesmith.queue_store import (
    DEFAULT_NIGHT_STATE_PATH,
    DEFAULT_SEED_PATH,
    DEFAULT_TRIAGE_LOG_PATH,
    QueueRequest,
    QueueSnapshot,
    QueueStore,
    QueueValidationError,
    in_flight_by_engine,
)
from issuesmith.queue_triage import (
    READY_LABEL,
    append_cas_conflict_log,
    append_circuit_open_log,
    apply_deterministic_order_constraints,
    apply_priority_bucket_order,
    comment_marker,
    deterministic_decision,
    deterministic_order,
    get_terminal_without_merge,
    has_marker_comment,
    is_circuit_open,
    label_names,
    load_seed_entries,
    parse_frontmatter_fields,
    seed_window,
    triage,
)
from issuesmith.targets import _normalize_allow_paths

logger = logging.getLogger(__name__)

_cfg = get_config()
TZ = ZoneInfo(_cfg.timezone)
REPO = _cfg.repo

REPO_ROOT = _cfg.root
JOBS_DIR = _cfg.paths.exec_jsonl.parent
DONE_DIR = _cfg.paths.done_dir
EXEC_PATH = _cfg.paths.exec_jsonl
QUOTA_STATE_PATH = _cfg.paths.quota_state
BRAKE_STATE_PATH = _cfg.paths.brake_state or _cfg.paths.quota_state


def _configured_phases():
    """Return config phases."""
    return get_config().phases


def _phase_label_maps() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    cfg = get_config()
    ready: dict[str, str] = {}
    running: dict[str, str] = {}
    done: dict[str, str] = {}
    for ph in cfg.phases:
        r, run, d = cfg.phase_labels(ph.name)
        ready[ph.name] = r
        running[ph.name] = run
        done[ph.name] = d
    return ready, running, done


def _all_ready_running_labels() -> set[str]:
    ready, running, _ = _phase_label_maps()
    return {lab for lab in (*ready.values(), *running.values()) if lab}


def _non_writing_done_labels() -> list[tuple[str, str, str]]:
    cfg = get_config()
    out: list[tuple[str, str, str]] = []
    for ph in cfg.phases:
        if not ph.writes_files:
            out.append(cfg.phase_labels(ph.name))
    return out


def _primary_impl_phase_name() -> str | None:
    for ph in get_config().phases:
        if (
            ph.role == "implementation"
            and ph.writes_files
            and "closing_pr_exists" not in ph.advance_when
        ):
            return ph.name
    return None


def _closing_phase_name() -> str | None:
    for ph in get_config().phases:
        if "closing_pr_exists" in ph.advance_when:
            return ph.name
    return None


def _design_phase_name() -> str | None:
    design = get_config().design_phase()
    return design.name if design is not None else None


def _phase_role_map() -> dict[str, str]:
    return {p.name: p.role for p in _configured_phases()}


def __getattr__(name: str) -> Any:
    if name == "PHASE_ROLE":
        return _phase_role_map()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class DispatchResult:
    dispatched: bool
    issue: int | None = None
    label: str | None = None
    request_id: str | None = None
    reason: str = ""


def _now_jst() -> datetime:
    return datetime.now(TZ)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def _in_window(now: datetime, start_str: str, end_str: str) -> bool:
    start = _parse_hhmm(start_str)
    end = _parse_hhmm(end_str)
    current = now.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _iter_issuesmith_exec_uuids() -> list[str]:
    return [uuid for uuid, _issue in _iter_issuesmith_exec_records()]


def _issue_from_idempotency_key(key: str) -> int | None:
    """Extract the issue number from ``issuesmith:<handler>:<issue>[:<generation>]``.

    Until 2026-09-09 the last element was taken as the issue, so a generation key
    (``issuesmith:impl:2959:1``, added by ghdag on redispatch) was misread as issue=1
    and blocked ``_dispatch_pipeline_ready`` as an "orphan" not in in_flight.
    """
    parts = key.split(":")
    if len(parts) < 3 or parts[0] != WORKFLOW_NAME:
        return None
    issue_part = parts[2]
    return int(issue_part) if issue_part.isdigit() else None


def _iter_issuesmith_exec_records() -> list[tuple[str, int | None]]:
    """Return (uuid, issue_number) for every issuesmith-prefixed exec.jsonl row.

    ``idempotency_key`` has the form ``issuesmith:<phase>:<issue_number>`` (e.g.
    ``issuesmith:impl:2969``). issue_number is None when the last element is not a
    number (currently issuesmith-prefixed keys always end with the issue number).
    """
    if not EXEC_PATH.exists():
        return []
    records: list[tuple[str, int | None]] = []
    for raw in EXEC_PATH.read_text(encoding="utf-8").splitlines():
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
        uuid = row.get("uuid")
        if idempotency_key.startswith("issuesmith:") and isinstance(uuid, str) and uuid:
            records.append((uuid, _issue_from_idempotency_key(idempotency_key)))
    return records


def _issue_has_incomplete_exec(issue_number: int) -> bool:
    """True when any issuesmith exec UUID for ``issue_number`` lacks a DONE marker.

    Used by design-slot in_flight release so a brief post-design-phase window
    (before the impl watcher dispatch) does not drop tracking
    while brushup or impl DAG rows are still pending (#3092).
    """
    for uuid, issue in _iter_issuesmith_exec_records():
        if issue != issue_number:
            continue
        if not (DONE_DIR / uuid).exists():
            return True
    return False


def _issue_exec_all_done(issue_number: int) -> bool:
    """True when at least one exec record exists for ``issue_number`` and all are DONE.

    Zero records returns False to avoid releasing a design slot before B1 even starts.
    """
    found = False
    for uuid, issue in _iter_issuesmith_exec_records():
        if issue != issue_number:
            continue
        found = True
        if not (DONE_DIR / uuid).exists():
            return False
    return found


def _latest_done_mtime() -> float | None:
    if not DONE_DIR.exists():
        return None
    mtimes = [p.stat().st_mtime for p in DONE_DIR.iterdir() if p.is_file()]
    if not mtimes:
        return None
    return max(mtimes)


def _issuesmith_latest_done_mtime() -> float | None:
    uuids = _iter_issuesmith_exec_uuids()
    if not uuids:
        return None
    mtimes: list[float] = []
    for uuid in uuids:
        done_path = DONE_DIR / uuid
        if done_path.exists():
            mtimes.append(done_path.stat().st_mtime)
    if not mtimes:
        return None
    return max(mtimes)


def _pipeline_idle_enough(idle_minutes: int, now: datetime) -> bool:
    """Legacy idle helper (no in-flight awareness). Prefer _pipeline_idle_enough_v2."""
    uuids = _iter_issuesmith_exec_uuids()
    for uuid in uuids:
        if not (DONE_DIR / uuid).exists():
            return False
    latest = _latest_done_mtime()
    if latest is None:
        return False
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


def _pipeline_idle_enough_v2(
    snap: QueueSnapshot, idle_minutes: int, now: datetime
) -> bool:
    if snap.in_flight:
        return False
    uuids = _iter_issuesmith_exec_uuids()
    for uuid in uuids:
        if not (DONE_DIR / uuid).exists():
            return False
    latest = _issuesmith_latest_done_mtime()
    if latest is None:
        return True
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


def _dispatch_pipeline_ready(
    snap: QueueSnapshot, idle_minutes: int, now: datetime
) -> bool:
    """Admission idle gate.

    Requires "no mid-flight steps", but tolerates unfinished steps of an issue
    already tracked in ``snap.in_flight`` since those are expected to be unfinished
    (after the per-engine concurrency of #2867, while other issues ran in parallel,
    the running issue's own pending follow-up steps were misjudged as "pipeline not
    idle" and also blocked new dispatches on other engines). Unfinished steps of
    issues not in in_flight indicate an in_flight leak or orphan tasks after a
    crash, so they still block.

    However, an issue not in in_flight does not block while its DAG is running
    (#3662). The same applies to a CLOSED issue whose DAG is still running (e.g.
    GitHub auto-closed it right after merge). A pending DAG (gap / orphan) still
    blocks as before.
    """
    from issuesmith.observe.dag_state import load_dag_states

    in_flight_issues = {
        entry.get("issue") for entry in snap.in_flight if isinstance(entry, dict)
    }
    dag_states = None  # loaded lazily: only needed when an unfinished row is off in_flight
    for uuid, issue_number in _iter_issuesmith_exec_records():
        if issue_number in in_flight_issues:
            continue
        if not (DONE_DIR / uuid).exists():
            if dag_states is None:
                dag_states = load_dag_states(EXEC_PATH, DONE_DIR, DONE_DIR.parent / "running")
            state = dag_states.get(issue_number) if issue_number is not None else None
            if state is not None and state.status == "running":
                continue
            return False
    if snap.in_flight:
        return True
    latest = _issuesmith_latest_done_mtime()
    if latest is None:
        return True
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


ENGINE_STATE_PATH = _cfg.paths.engine_state
_DEFAULT_ROLE_ENGINES = {"design": "claude", "implementation": "claude"}


def _required_engines(engine_state_path: Path | None = None) -> dict[str, str]:
    """Read role -> engine from .pipeline-state/issuesmith-engine.yml.

    Falls back to the default (claude for both design and implementation) when unreadable.
    """
    path = engine_state_path or ENGINE_STATE_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return dict(_DEFAULT_ROLE_ENGINES)
    result: dict[str, str] = {}
    for role, default in _DEFAULT_ROLE_ENGINES.items():
        value = data.get(role) if isinstance(data, dict) else None
        engine = value.get("engine") if isinstance(value, dict) else None
        result[role] = str(engine) if engine else default
    return result


def _resolve_engine(phase: str, engine_state_path: Path | None = None) -> str:
    role = _phase_role_map()[phase]
    return _required_engines(engine_state_path)[role]


def _serial_concurrency() -> bool:
    concurrency = get_config().concurrency
    if concurrency.per_engine:
        return False
    return concurrency.default <= 1


_TICK_ISSUE_FIELDS = ("state", "labels", "title", "body", "number", "milestone")
_OPEN_ISSUES_PATH = "issues?state=open&per_page=100"
_OPEN_ISSUES_QUERY = re.compile(r"^issues\?(?P<query>[^/]*)$")
# ForgePort methods that mutate Issue state/labels or the open-issue set.
_ISSUE_WRITE_METHODS = frozenset({
    "issue_update", "issue_close", "reopen_issue", "remove_label", "update_label",
    "issue_create",
})


class _TickCachedForge:
    """Tick-scoped ForgePort proxy that memoizes Issue reads (#3759).

    ``issue_get`` fetches the union of fields the tick needs once per Issue
    (errors are memoized too). Open-issue list requests share one unfiltered
    ``issues?state=open`` fetch and apply ``labels=`` locally. Issue writes
    drop the affected cache entries; every other call is delegated as-is.
    """

    def __init__(self, inner: ForgePort) -> None:
        self._inner = inner
        self._issues: dict[int, dict[str, Any] | Exception] = {}
        self._open_lists: dict[bool, Any] = {}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if name not in _ISSUE_WRITE_METHODS or not callable(attr):
            return attr

        def _write(number: int, *args: Any, **kwargs: Any) -> Any:
            self._issues.pop(number, None)
            self._open_lists.clear()
            return attr(number, *args, **kwargs)

        return _write

    def issue_get(self, number: int, fields: list[str] | None = None) -> dict:
        if fields is None or not set(fields) <= set(_TICK_ISSUE_FIELDS):
            return self._inner.issue_get(number, fields=fields)
        cached = self._issues.get(number)
        if cached is None:
            try:
                cached = self._inner.issue_get(number, fields=list(_TICK_ISSUE_FIELDS))
            except Exception as exc:
                cached = exc
            self._issues[number] = cached
        if isinstance(cached, Exception):
            raise cached
        return dict(cached)

    def api_request(self, path: str, *, method: str = "GET", **kwargs: Any) -> Any:
        labels = self._open_issues_labels(path) if method.upper() == "GET" else None
        if labels is None or set(kwargs) - {"paginate"}:
            if method.upper() != "GET":
                self._issues.clear()
                self._open_lists.clear()
            return api_request(self._inner, path, method=method, **kwargs)
        paginate = bool(kwargs.get("paginate", False))
        cached = self._open_lists.get(paginate)
        if cached is None:
            try:
                cached = api_request(self._inner, _OPEN_ISSUES_PATH, paginate=paginate)
            except Exception as exc:
                cached = exc
            self._open_lists[paginate] = cached
        if isinstance(cached, Exception):
            raise cached
        if not isinstance(cached, list):
            return cached
        return [
            item for item in cached
            if not labels or (isinstance(item, dict) and labels <= label_names(item))
        ]

    @staticmethod
    def _open_issues_labels(path: str) -> set[str] | None:
        """Return the ``labels=`` filter for an open-issue list path, else None."""
        m = _OPEN_ISSUES_QUERY.match(path)
        if m is None:
            return None
        params: dict[str, str] = {}
        for part in m.group("query").split("&"):
            key, _, value = part.partition("=")
            params[key] = value
        if params.get("state") != "open" or params.get("per_page") != "100":
            return None
        if set(params) - {"state", "per_page", "labels"}:
            return None
        return {lab for lab in params.get("labels", "").split(",") if lab}


def _issue_state_is_terminal(issue: dict[str, Any]) -> bool:
    labels = label_names(issue)
    state = str(issue.get("state", "")).upper()
    terminal = set(get_config().terminal_labels)
    return state == "CLOSED" and (
        bool(labels & terminal) or bool(labels & get_terminal_without_merge())
    )


def _issue_is_terminal(client: ForgePort, issue_number: int) -> bool:
    try:
        issue = client.issue_get(issue_number, fields=["state", "labels"])
    except Exception:
        return False
    return _issue_state_is_terminal(issue)


def _issue_target_meta(issue: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Return (target_repo, allow_paths) from issue body YAML."""
    meta = parse_frontmatter_fields(str(issue.get("body") or ""))
    repo = str(meta.get("target_repo") or "").strip()
    paths = _normalize_allow_paths(meta.get("allow_paths", ()))
    return repo, paths


def _phase_writes_files(phase_name: str | None) -> bool:
    if not isinstance(phase_name, str) or not phase_name:
        return True
    try:
        return get_config().phase(phase_name).writes_files
    except KeyError:
        return True


def _writes_files(entry: dict[str, Any]) -> bool:
    """True unless the in_flight entry is known to be a non-file-writing phase run."""
    phase = entry.get("phase")
    if isinstance(phase, str):
        return _phase_writes_files(phase)
    if entry.get("role") == "design" and phase is None:
        design = get_config().design_phase()
        if design is not None:
            return design.writes_files
        return False
    return True


def _allow_paths_conflict(
    candidate_repo: str,
    candidate_paths: tuple[str, ...],
    in_flight: list[dict[str, Any]],
    *,
    candidate_phase: str | None = None,
) -> int | None:
    """Return conflicting in_flight issue number, or None if no conflict.

    allow_paths conflicts only matter between runs that write files of the
    same target repo: draft (B1) and sub (SUB1) only edit Issue bodies, so a
    draft / sub candidate never conflicts and draft / sub runs in flight never
    block anyone. Legacy entries without ``target_repo`` / ``allow_paths`` are
    treated as conflicts (fail closed) until they leave in_flight naturally.
    """
    if _phase_writes_files(candidate_phase) is False:
        return None
    for entry in in_flight:
        if not _writes_files(entry):
            continue
        entry_repo = entry.get("target_repo")
        if not isinstance(entry_repo, str) or not entry_repo.strip():
            issue = entry.get("issue")
            return int(issue) if isinstance(issue, int) else None
        if entry_repo != candidate_repo:
            continue
        raw_paths = entry.get("allow_paths")
        if not raw_paths:
            issue = entry.get("issue")
            return int(issue) if isinstance(issue, int) else None
        entry_paths = _normalize_allow_paths(raw_paths)
        for cpath in candidate_paths:
            for epath in entry_paths:
                if fnmatch.fnmatch(cpath, epath) or fnmatch.fnmatch(epath, cpath):
                    issue = entry.get("issue")
                    return int(issue) if isinstance(issue, int) else None
    return None


def _conflict_overlap_path(
    candidate_paths: tuple[str, ...],
    entry: dict[str, Any],
) -> str:
    raw_paths = entry.get("allow_paths")
    if not raw_paths:
        return "(legacy in_flight)"
    entry_paths = _normalize_allow_paths(raw_paths)
    for cpath in candidate_paths:
        for epath in entry_paths:
            if fnmatch.fnmatch(cpath, epath) or fnmatch.fnmatch(epath, cpath):
                return epath if fnmatch.fnmatch(cpath, epath) else cpath
    return entry_paths[0] if entry_paths else "?"


def _in_flight_should_release(client: ForgePort, entry: dict[str, Any]) -> bool:
    """True when an in_flight entry should be dropped.

    Terminal issues always release. Design-slot entries also release after
    the design phase completes once the impl phase has not yet started
    (absorbs former milestone C0), but only when the issue has no incomplete
    issuesmith exec records (#3092).
    """
    issue_num = entry.get("issue")
    if not isinstance(issue_num, int):
        return False
    try:
        issue = client.issue_get(issue_num, fields=["state", "labels"])
    except Exception:
        return False
    if _issue_state_is_terminal(issue):
        return True
    labels = label_names(issue)
    role = entry.get("role")
    design_name = _design_phase_name()
    for ready, running, done in _non_writing_done_labels():
        if design_name and done == get_config().phase_labels(design_name)[2]:
            continue
        if done in labels and not (labels & {ready, running} - {""}):
            return True
    _, _, design_done = (
        get_config().phase_labels(design_name) if design_name else ("", "", "")
    )
    if role == "design" and design_done and design_done not in labels:
        active = _all_ready_running_labels()
        if not (labels & active) and _issue_exec_all_done(issue_num):
            return True
    if not design_done or design_done not in labels:
        return False
    busy: set[str] = set()
    impl = _primary_impl_phase_name()
    if impl:
        ready, running, _ = get_config().phase_labels(impl)
        busy.update({ready, running})
    if labels & busy:
        return False
    if _issue_has_incomplete_exec(issue_num):
        return False
    if role is not None and role != "design":
        return False
    return True


def _recheck_dependents_after_merge(
    client: ForgePort, issue_number: int, open_issues: list[dict[str, Any]]
) -> None:
    """When a released in_flight Issue is merge-done, re-run the scope_coupling deletion
    check on the open Issues that depend on it (nexus #3953)."""
    closing = _closing_phase_name()
    if closing is None:
        return
    _, _, merge_done = get_config().phase_labels(closing)
    if not merge_done:
        return
    try:
        issue = client.issue_get(issue_number, fields=["state", "labels"])
        if merge_done not in label_names(issue):
            return
        on_dep_merge_done(issue_number, dependents_of(issue_number, open_issues), client=client)
    except Exception as exc:
        print(f"warning: scope_coupling recheck after #{issue_number} failed: {exc}", file=sys.stderr)


def _release_finished_in_flight(
    store: QueueStore, client: ForgePort, open_issues: list[dict[str, Any]]
) -> None:
    for entry in list(store.snapshot().in_flight):
        if _in_flight_should_release(client, entry):
            issue_num = entry.get("issue")
            if isinstance(issue_num, int):
                store.remove_in_flight(issue_num)
                _recheck_dependents_after_merge(client, issue_num, open_issues)


def _find_untracked_running(client: ForgePort, snap: QueueSnapshot) -> list[int]:
    """Return impl-phase running issue numbers absent from ``snap.in_flight`` with a live DAG."""
    from issuesmith.observe.dag_state import load_dag_states

    tracked = {
        entry.get("issue") for entry in snap.in_flight if isinstance(entry, dict)
    }
    dag_states = load_dag_states(EXEC_PATH, DONE_DIR, DONE_DIR.parent / "running")
    candidates = {
        num
        for num, state in dag_states.items()
        if state.status == "running" and num not in tracked
    }
    # Stale running labels without a live DAG never reach the API (#3759).
    if not candidates:
        return []
    impl_phase = _primary_impl_phase_name()
    if impl_phase is None:
        return []
    _, running_map, _ = _phase_label_maps()
    running_label = running_map.get(impl_phase, "")
    if not running_label:
        return []
    try:
        issues = client.list_issues(running_label, state="open")
    except Exception:
        return []
    if not isinstance(issues, list):
        return []
    untracked = {
        num
        for issue in issues
        if isinstance(issue, dict)
        and isinstance(num := issue.get("number"), int)
        and num in candidates
    }
    return sorted(untracked)


def _recover_untracked_in_flight(
    client: ForgePort, store: QueueStore, snap: QueueSnapshot
) -> int:
    """Re-register impl-phase running Issues missing from in_flight (#3092 AC-2).

    Watcher dispatches impl DAGs without queue participation; if a design slot
    was released in the post-design-phase gap, those Issues vanish from tracking
    and orphan-gate the whole queue. Recover before ``_dispatch_pipeline_ready``.
    """
    untracked = _find_untracked_running(client, snap)
    recovered = 0
    for issue_num in untracked:
        try:
            issue = client.issue_get(
                issue_num, fields=["state", "labels", "body", "number"]
            )
        except Exception as exc:
            logger.warning(
                "in_flight_recovered skipped issue=#%s: issue_get failed: %s",
                issue_num,
                exc,
            )
            continue
        target_repo, allow_paths = _issue_target_meta(issue)
        impl_phase = _primary_impl_phase_name() or get_config().phases[0].name
        engine = _resolve_engine(impl_phase)
        store.add_in_flight(
            issue_num,
            engine,
            role="implementation",
            allow_paths=allow_paths,
            target_repo=target_repo or None,
        )
        logger.info("in_flight_recovered issue=#%s", issue_num)
        recovered += 1
    return recovered



def _required_engines_paused(
    quota_path: Path | None = None,
    engine_state_path: Path | None = None,
    role: str | None = None,
    brake_path: Path | None = None,
) -> list[str]:
    """Return the paused engines among those required for dispatch.

    Judged on the union of the global quota gate and the budget gate: an engine
    ``paused`` in either one is included.

    With ``role`` only that role's engine is considered; without it, the union of
    the design / implementation roles (backward compatible). ``dispatch_one`` calls
    it per phase with ``role=_phase_role_map()[phase]`` (#3091).

    Existing callers that pass only ``quota_path`` and omit ``brake_path`` use the
    same path for both gates to keep tests compatible. With the same path the
    snapshot is read only once.
    """
    resolved_quota = quota_path or QUOTA_STATE_PATH
    if brake_path is not None:
        resolved_brake = brake_path
    elif quota_path is not None:
        resolved_brake = quota_path
    else:
        resolved_brake = BRAKE_STATE_PATH

    if resolved_quota == resolved_brake:
        snapshots = [QuotaGate(state_path=resolved_quota).snapshot()]
    else:
        snapshots = [
            QuotaGate(state_path=resolved_quota).snapshot(),
            QuotaGate(state_path=resolved_brake).snapshot(),
        ]

    if all(not snapshot.engines for snapshot in snapshots):
        return []

    role_map = _required_engines(engine_state_path)
    if role is not None:
        engine_name = role_map.get(role)
        required = {engine_name} if engine_name else set()
    else:
        required = set(role_map.values())

    paused: set[str] = set()
    for snapshot in snapshots:
        for name, engine in snapshot.engines.items():
            if name in required and engine.status == "paused":
                paused.add(name)
    return sorted(paused)


def _all_engines_paused(quota_path: Path | None = None) -> bool:
    """Backward-compatible wrapper: True if any required role's engine is paused."""
    return bool(_required_engines_paused(quota_path))


def _source_in_window(source: str, actor_kind: str, now: datetime, start: str, end: str) -> bool:
    if actor_kind == "human":
        return True
    if source in ("release-watcher", "night-queue-seed", "night-queue"):
        return _in_window(now, start, end)
    # Other automation: allow always unless configured otherwise.
    return True


def _format_in_flight_status(snap: QueueSnapshot) -> str:
    concurrency = get_config().concurrency
    role_map = _required_engines()
    counts = in_flight_by_engine(snap.in_flight, role_engine_map=role_map)
    engines = sorted(set(counts) | set(concurrency.per_engine))
    if not engines:
        engines = sorted(role_map.values())
    held = in_flight_by_engine(
        [{k: v for k, v in e.items() if k != "held"} for e in snap.in_flight if e.get("held")],
        role_engine_map=role_map,
    )
    parts: list[str] = []
    for engine in engines:
        limit = concurrency.limit(engine)
        held_part = f" (+{held[engine]} held)" if held.get(engine) else ""
        parts.append(f"{engine}: {counts.get(engine, 0)}/{limit}{held_part}")
    return "{" + ", ".join(parts) + "}"


def _closes_issue_marker(issue_number: int) -> re.Pattern[str]:
    # Digit boundary: avoid Closes #12 matching #123.
    # publish.py no longer emits "Closes" (to avoid GitHub auto-close side effects;
    # premature closes were observed in #2852 / #2873). Match both "Closes" and "Refs"
    # so this still finds "the PR linked to this issue" for both old-generation
    # (Closes) and new-generation (Refs) PRs.
    return re.compile(rf"(?i)\b(?:closes|refs)\s+#{issue_number}(?!\d)")


def _find_open_prs_closing_issue(client: ForgePort, issue_number: int) -> list[dict[str, Any]]:
    """Find open PRs whose title/body contain ``Closes #N``.

    Production ``pr_list(search=...)`` only filters title/head, and
    ``_normalize_prs`` strips ``body``. Mirror loops_host: list open PRs, then
    ``pr_get`` for each candidate to read the body.
    """
    marker = _closes_issue_marker(issue_number)
    try:
        prs = client.pr_list(state="open", limit=100)
    except Exception:
        return []
    if not isinstance(prs, list):
        return []
    matched: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        if marker.search(title) or marker.search(body):
            matched.append(pr)
            continue
        number = pr.get("number")
        if not isinstance(number, int):
            continue
        try:
            detail = client.pr_get(number)
        except Exception:
            continue
        if not isinstance(detail, dict):
            continue
        d_title = str(detail.get("title") or "")
        d_body = str(detail.get("body") or "")
        if marker.search(d_title) or marker.search(d_body):
            matched.append(detail)
    return matched


def _pr_is_merged(pr: dict[str, Any]) -> bool:
    """True if PR dict looks merged (GraphQL MERGED, REST merged_at, or merged flag)."""
    if pr.get("merged") is True:
        return True
    for key in ("mergedAt", "merged_at"):
        val = pr.get(key)
        if isinstance(val, str) and val.strip():
            return True
    return str(pr.get("state") or "").upper() == "MERGED"


def _find_merged_prs_closing_issue(client: ForgePort, issue_number: int) -> list[dict[str, Any]]:
    """Find merged PRs whose title/body contain ``Closes #N``.

    ``pr_list(state="closed")`` strips ``body`` and often omits merge metadata.
    Use ``pr_get`` for body and ``api_request("pulls/{n}")`` when merge status
    is missing from the normalized list (production GitHubClient via get_forge).
    """
    marker = _closes_issue_marker(issue_number)
    try:
        prs = client.pr_list(state="closed", limit=100)
    except Exception:
        return []
    if not isinstance(prs, list):
        return []
    matched: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if not isinstance(number, int):
            continue

        detail: dict[str, Any] = dict(pr)
        if not _pr_is_merged(detail):
            try:
                raw = api_request(client, f"pulls/{number}")
            except Exception:
                raw = None
            if isinstance(raw, dict) and _pr_is_merged(raw):
                detail = {**detail, **raw}
            else:
                continue

        title = str(detail.get("title") or "")
        body = str(detail.get("body") or "")
        if marker.search(title) or marker.search(body):
            matched.append(detail)
            continue
        try:
            fetched = client.pr_get(number)
        except Exception:
            continue
        if not isinstance(fetched, dict):
            continue
        d_title = str(fetched.get("title") or "")
        d_body = str(fetched.get("body") or "")
        if marker.search(d_title) or marker.search(d_body):
            matched.append({**detail, **fetched})
    return matched


def _list_open_issues(client: ForgePort) -> list[dict[str, Any]]:
    """List all open Issues (excluding PRs) via the production forge client contract.

    ``list_issues`` requires a label and cannot scan the whole repo for supersede.
    Use ``api_request("issues?state=open&...")`` which returns raw GitHub JSON.
    """
    raw: Any = None
    try:
        raw = api_request(client, "issues?state=open&per_page=100", paginate=True)
    except Exception:
        try:
            raw = api_request(client, "issues?state=open&per_page=100")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        # GitHub /issues includes pull requests; skip them for supersede.
        if item.get("pull_request") is not None:
            continue
        state = str(item.get("state") or "OPEN").upper()
        out.append(
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "state": state,
                "body": item.get("body") or "",
                "labels": item.get("labels") or [],
            }
        )
    return out


WORKFLOW_NAME = "issuesmith"
MAX_REPAIR_PER_REQUEST = 1


def _step_to_handler_map() -> dict[str, str]:
    """Map step_id to handler_name by reading the workflow YAML."""
    from issuesmith.config import get_config
    workflow_path = get_config().paths.workflow
    if not workflow_path.exists():
        return {}
    try:
        import yaml as _yaml
        yml = _yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        result: dict[str, str] = {}
        for handler_name, handler_cfg in (yml.get("handlers") or {}).items():
            if not isinstance(handler_cfg, dict):
                continue
            for step in (handler_cfg.get("steps") or []):
                if not isinstance(step, dict):
                    continue
                sid = step.get("id")
                if isinstance(sid, str):
                    result[sid] = handler_name
        return result
    except Exception:
        return {}


def _phase_handler_map() -> dict[str, str]:
    """Return phase -> handler mapping from config."""
    return {p.name: p.handler for p in get_config().phases if p.handler}


_MAX_GENERATION_SCAN = 16


def _handler_key_consumed(handler: str, issue: int) -> bool:
    """Whether this handler's idempotency key (including generations) is consumed in exec.jsonl.

    If consumed, re-adding the ready label does not start the ghdag watcher
    ("dispatch skipped (already dispatched)"; observed 2026-09-09 while recovering the
    CP2 FAIL of #2980). The generation must then be bumped to start it again.
    """
    if not EXEC_PATH.exists():
        return False
    from ghdag.io import exec_jsonl

    base = f"{WORKFLOW_NAME}:{handler}:{issue}"
    keys = [base] + [f"{base}:{gen}" for gen in range(1, _MAX_GENERATION_SCAN)]
    return any(not exec_jsonl.check_idempotency(EXEC_PATH, key) for key in keys)


def _trigger_ghdag_redispatch(issue: int, handler: str, reason: str) -> int:
    """Bump the ghdag generation and start the handler (same as `ghdag trigger --redispatch`)."""
    cmd = [
        sys.executable,
        "-m",
        "ghdag",
        "trigger",
        str(issue),
        "--handler",
        handler,
        "--workflow",
        WORKFLOW_NAME,
        "--exec-md",
        str(EXEC_PATH),
        "--redispatch",
        "--reason",
        reason,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        print(
            f"[issuesmith-queue] ghdag redispatch failed for #{issue} handler={handler} "
            f"rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}",
            file=sys.stderr,
        )
    return int(proc.returncode)


def handler_for_failed_step(failed_step: str, labels: set[str]) -> str:
    cfg = get_config()
    step_map = _step_to_handler_map()
    h = step_map.get(failed_step)
    if h:
        return h
    for ph in cfg.phases:
        ready, running, _ = cfg.phase_labels(ph.name)
        if (running in labels) or (ready in labels):
            if ph.handler:
                return ph.handler
    for ph in cfg.phases:
        if ph.role == "implementation" and ph.writes_files and ph.handler:
            return ph.handler
    return cfg.phases[0].handler if cfg.phases else ""


def infer_redispatch_phase(failed_step: str, labels: set[str]) -> str:
    cfg = get_config()
    step_map = _step_to_handler_map()
    handler = step_map.get(failed_step)
    if handler:
        phase_name = cfg.phase_for_handler(handler)
        if phase_name:
            return phase_name

    design = cfg.design_phase()
    if design is not None:
        design_done = cfg.phase_labels(design.name)[2]
        design_ready, design_running, _ = cfg.phase_labels(design.name)
        step_phase = cfg.phase_for_handler(handler) if handler else None
        if step_phase == design.name or (
            design_done
            and design_done not in labels
            and ((design_ready in labels) or (design_running in labels))
        ):
            return design.name

    for ph in cfg.phases:
        if "closing_pr_exists" in ph.advance_when:
            ready, running, _ = cfg.phase_labels(ph.name)
            if (ready in labels) or (running in labels):
                return ph.name

    for ph in cfg.phases:
        if ph.role == "implementation" and ph.writes_files:
            if "closing_pr_exists" not in ph.advance_when:
                return ph.name
    return cfg.phases[-1].name if cfg.phases else ""


def redispatch_label_plan(phase: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (labels that must be present, labels to remove) for redispatch."""
    cfg = get_config()
    ph = cfg.phase(phase)
    ready, running, done = cfg.phase_labels(ph.name)
    return cfg.required_labels(ph.name), frozenset({ready, running, done})


def apply_redispatch_labels(
    client: ForgePort,
    issue_number: int,
    phase: str,
    current_labels: set[str],
) -> bool:
    required, to_remove = redispatch_label_plan(phase)
    labels_add = sorted(lab for lab in required if lab not in current_labels)
    labels_remove = sorted(lab for lab in to_remove if lab in current_labels)
    if not labels_add and not labels_remove:
        return False
    client.issue_update(issue_number, labels_add=labels_add, labels_remove=labels_remove)
    return True


def phase_preconditions(
    phase: str, issue: dict[str, Any], client: ForgePort, issue_number: int
) -> tuple[bool, str]:
    from issuesmith.preconditions import PreconditionContext, evaluate

    cfg = get_config()
    try:
        ph = cfg.phase(phase)
    except KeyError:
        return False, f"unknown phase {phase}"
    labels = label_names(issue)
    ctx = PreconditionContext(
        issue=issue, labels=labels, client=client, issue_number=issue_number
    )
    return evaluate(ph, ctx, cfg)


def _ensure_comment(
    client: ForgePort,
    issue_number: int,
    request_id: str,
    outcome: str,
    body: str,
) -> None:
    try:
        comments = client.get_issue_comments(issue_number)
    except Exception:
        comments = []
    if not isinstance(comments, list):
        comments = []
    if has_marker_comment(comments, request_id, outcome):
        return
    marker = comment_marker(request_id, outcome)
    client.issue_comment(issue_number, f"{body}\n\n{marker}")


def _apply_repair(
    store: QueueStore,
    client: ForgePort,
    request_id: str,
    req: QueueRequest,
    issue: dict[str, Any],
    reason: str,
) -> None:
    snap = store.snapshot()
    count = int((snap.request_meta.get(request_id) or {}).get("repair_count", 0))
    if count >= MAX_REPAIR_PER_REQUEST:
        _apply_terminal(
            store,
            client,
            request_id,
            req.issue,
            "rejected",
            get_config().language.message("queue.repair_limit", reason=reason),
            add_rejected_label=True,
            comment=True,
        )
        return

    body = get_config().language.message("queue.intake_repair", reason=reason)
    _ensure_comment(client, req.issue, request_id, "repair", body)
    design_phase = _design_phase_name()
    if design_phase is None:
        return
    apply_redispatch_labels(client, req.issue, design_phase, label_names(issue))
    store.enqueue(
        issue=req.issue,
        phase=design_phase,
        source="queue-repair",
        actor_kind="automation",
        priority=req.priority,
        requested_by=[f"queue-repair:{request_id}"],
    )
    store.update_meta(
        request_id,
        {"repair_count": count + 1, "repair_reason": reason},
    )


def _apply_terminal(
    store: QueueStore,
    client: ForgePort,
    request_id: str,
    issue_number: int,
    decision_kind: str,
    reason: str,
    *,
    close_issue: bool = False,
    add_rejected_label: bool = False,
    comment: bool = True,
) -> None:
    meta = store.snapshot().request_meta.get(request_id) or {}
    if meta.get("outcome") == decision_kind:
        # Already terminal.
        return
    if comment:
        _ensure_comment(client, issue_number, request_id, decision_kind, reason)
    if add_rejected_label:
        try:
            client.issue_update(issue_number, labels_add=["issuesmith:rejected"], labels_remove=[])
        except Exception as exc:
            print(f"warning: failed to add rejected label: {exc}", file=sys.stderr)
    if decision_kind == "superseded":
        try:
            client.issue_update(issue_number, labels_add=["issuesmith:superseded"], labels_remove=[])
        except Exception as exc:
            print(f"warning: failed to add superseded label: {exc}", file=sys.stderr)
    if close_issue:
        try:
            client.issue_close(issue_number)
        except Exception as exc:
            print(f"warning: failed to close issue: {exc}", file=sys.stderr)
    store.complete(request_id, decision_kind, extra_meta={"reason": reason})


def ensure_seeds_enqueued(store: QueueStore, *, seed_path: Path | None = None, now: datetime | None = None) -> int:
    now = now or _now_jst()
    count = 0
    for entry in load_seed_entries(seed_path):
        result = store.enqueue(
            issue=int(entry["issue"]),
            phase=str(entry["phase"]),
            source="night-queue-seed",
            actor_kind="automation",
            priority="low",
            requested_by=["night-queue"],
            requested_at=now.isoformat(),
            seed_key=str(entry["seed_key"]),
        )
        if result.created:
            count += 1
    return count


def _github_api_brake_reason(now: datetime) -> str:
    """Return a skip reason when the GitHub API budget is below api_brake (#3769), else ''."""
    from issuesmith.config import ApiBreakConfig
    from issuesmith.quota_gate import is_github_api_low, read_github_api_state

    cfg = get_config()
    brake = getattr(cfg, "api_brake", None) or ApiBreakConfig()
    if not brake.enabled:
        return ""
    state = read_github_api_state(cfg.paths.exec_jsonl.parent / "audit.jsonl")
    if state is None or not is_github_api_low(state, brake.min_remaining, now):
        return ""
    reset_str = state.reset_at.astimezone(now.tzinfo).strftime("%H:%M")
    return f"github_api_low (remaining={state.remaining}, reset={reset_str})"


def dispatch_one(
    now: datetime | None = None,
    client: ForgePort | None = None,
    *,
    store: QueueStore | None = None,
    seed_path: Path | None = None,
    call_llm: Any = None,
    skip_seed: bool = False,
) -> DispatchResult:
    now = now or _now_jst()
    store = store or QueueStore()
    # One tick = one read per Issue / one open-issue list (#3759).
    client = _TickCachedForge(client or get_forge(repo=REPO))
    start, end, idle_minutes = seed_window(seed_path)

    if not skip_seed:
        ensure_seeds_enqueued(store, seed_path=seed_path, now=now)

    brake = _github_api_brake_reason(now)
    if brake:
        return DispatchResult(dispatched=False, reason=brake)

    advance_milestone_chains(store, client, get_config())

    # Fetch issues for active requests.
    snap = store.snapshot()
    issues: dict[int, dict[str, Any]] = {}
    for rid in list(snap.active_order):
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        if req.issue not in issues:
            try:
                data = client.issue_get(
                    req.issue, fields=["state", "labels", "title", "body", "number"]
                )
                data.setdefault("number", req.issue)
                issues[req.issue] = data
            except GitHubApiError as exc:
                if exc.status_code == 404:
                    store.complete(
                        rid,
                        "not_found",
                        extra_meta={"reason": f"issue #{req.issue} does not exist (404)"},
                    )
                    print(f"not_found: #{req.issue} (404)", file=sys.stderr)
                    continue
                print(f"warning: issue_get #{req.issue} failed: {exc}", file=sys.stderr)
                continue
            except Exception as exc:
                print(f"warning: issue_get #{req.issue} failed: {exc}", file=sys.stderr)
                continue

    # Supersede must scan ALL open Issues in the repo, not only those in the queue.
    open_issues = _list_open_issues(client)
    if not open_issues:
        open_issues = [
            iss for iss in issues.values() if str(iss.get("state", "")).upper() == "OPEN"
        ]
    else:
        seen = {i.get("number") for i in open_issues}
        for iss in issues.values():
            if str(iss.get("state", "")).upper() != "OPEN":
                continue
            num = iss.get("number")
            if num not in seen:
                open_issues.append(iss)

    # Deterministic decisions first.
    snap = store.snapshot()
    for rid in list(snap.active_order):
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        issue = issues.get(req.issue)
        if issue is None:
            continue
        decision = deterministic_decision(
            req,
            issue,
            open_issues=open_issues,
            force=store.is_force(snap, rid),
        )
        if decision.kind == "keep":
            continue
        if decision.kind == "repair":
            _apply_repair(store, client, rid, req, issue, decision.reason)
            continue
        _apply_terminal(
            store,
            client,
            rid,
            req.issue,
            decision.kind,
            decision.reason,
            close_issue=decision.close_issue,
            add_rejected_label=decision.add_rejected_label,
            comment=decision.comment,
        )

    snap = store.snapshot()
    triage_revision = snap.revision
    # Triage if needed (LLM outside lock conceptually — triage() does not hold store lock during LLM).
    if triage_revision > snap.last_triaged_revision and snap.active_order:
        _triage_log = (
            Path(os.environ["ISSUESMITH_QUEUE_DIR"]) / "issuesmith-triage.jsonl"
            if os.environ.get("ISSUESMITH_QUEUE_DIR")
            else DEFAULT_TRIAGE_LOG_PATH
        )
        triage_cfg = get_config().triage
        active_reqs = []
        for rid in snap.active_order:
            req = store.effective_request(snap, rid)
            if req is not None:
                active_reqs.append(req)
        fallback_order = deterministic_order(active_reqs)
        fallback_order = apply_deterministic_order_constraints(
            fallback_order,
            {r.request_id: r for r in active_reqs},
            now=now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc),
        )
        # AC-2: apply priority (deterministic) before waiting on LLM.
        adopted_fallback = store.replace_order(triage_revision, fallback_order)
        if not adopted_fallback:
            store.mark_triaged(triage_revision)
        else:
            now_utc = (
                now.astimezone(timezone.utc)
                if now.tzinfo
                else now.replace(tzinfo=timezone.utc)
            )
            if not triage_cfg.enabled:
                pass  # deterministic order already applied; skip LLM
            elif is_circuit_open(
                _triage_log,
                triage_cfg.circuit_breaker_threshold,
                triage_cfg.circuit_breaker_reset_seconds,
                now=now_utc,
            ):
                append_circuit_open_log(
                    path=_triage_log,
                    order=fallback_order,
                    revision=store.snapshot().revision,
                    now=now_utc,
                )
            else:
                snap2 = store.snapshot()
                # replace_order marked last_triaged==revision; force LLM path.
                llm_snap = replace(snap2, last_triaged_revision=snap2.revision - 1)
                result = triage(
                    llm_snap,
                    issues=issues,
                    store=store,
                    call_llm=call_llm,
                    now=now,
                    triage_log_path=_triage_log,
                    timeout=triage_cfg.timeout,
                    engine=triage_cfg.engine,
                    model=triage_cfg.model,
                    body_chars=triage_cfg.body_chars,
                )
                if result.adopted:
                    active_for_bucket = []
                    for rid in snap2.active_order:
                        req = store.effective_request(snap2, rid)
                        if req is not None:
                            active_for_bucket.append(req)
                    bucket_order = apply_priority_bucket_order(
                        result.order, active_for_bucket
                    )
                    active_set = set(snap2.active_order)
                    new_order = [rid for rid in bucket_order if rid in active_set]
                    for rid in snap2.active_order:
                        if rid not in new_order:
                            new_order.append(rid)
                    adopted_cas = store.replace_order(snap2.revision, new_order)
                    if not adopted_cas:
                        latest = store.snapshot()
                        latest_set = set(latest.active_order)
                        retry_order = [rid for rid in bucket_order if rid in latest_set]
                        for rid in latest.active_order:
                            if rid not in retry_order:
                                retry_order.append(rid)
                        adopted_cas = store.replace_order(latest.revision, retry_order)
                        if not adopted_cas:
                            append_cas_conflict_log(
                                path=_triage_log,
                                order=bucket_order,
                                revision=latest.revision,
                                now=now_utc,
                            )
                            store.mark_triaged(latest.revision)
                # Apply LLM rejects after ordering CAS.
                for d in result.decisions:
                    if d.decision != "reject":
                        continue
                    cur = store.snapshot()
                    req = store.effective_request(cur, d.request_id)
                    if req is None or d.request_id not in cur.active_order:
                        continue
                    _apply_terminal(
                        store,
                        client,
                        d.request_id,
                        req.issue,
                        "rejected",
                        d.reason,
                        close_issue=False,
                        add_rejected_label=True,
                        comment=True,
                    )

    snap = store.snapshot()
    # Release finished in_flight even when the queue is empty (absorbs milestone C0).
    if snap.in_flight:
        with store.dispatch_lock():
            _release_finished_in_flight(store, client, open_issues)
            snap = store.snapshot()

    if not snap.active_order:
        return DispatchResult(False, reason="empty queue")

    with store.dispatch_lock():
        _release_finished_in_flight(store, client, open_issues)
        snap = store.snapshot()

        if snap.halt:
            # Legacy halts (no observe event) may self-resolve when the triggering
            # condition clears (e.g. in_flight drains after last_issue was OPEN).
            if snap.halt_event is None:
                _reason = snap.halt_reason or ""
                _resolved = False
                if "is still OPEN" in _reason:
                    _resolved = len(snap.in_flight) == 0
                elif "without terminal label" in _reason:
                    _m = re.search(r"#(\d+)", _reason)
                    if _m:
                        _resolved = _issue_is_terminal(client, int(_m.group(1)))
                if _resolved:
                    store.clear_halt()
                    snap = store.snapshot()

            if snap.halt:
                halt_scope = snap.halt_scope
                if halt_scope == "all":
                    return DispatchResult(False, reason=f"halted: {snap.halt_reason}")
                # Non-"all" scopes are checked per-request in the dispatch loop below.

        if snap.last_issue is not None:
            try:
                last = client.issue_get(snap.last_issue, fields=["state", "labels"])
            except Exception as exc:
                return DispatchResult(False, reason=f"last_issue fetch failed: {exc}")
            last_labels = label_names(last)
            last_state = str(last.get("state", "")).upper()
            terminal_ok = _issue_state_is_terminal(last)
            if not terminal_ok and milestone_last_issue_terminal_ok(last_labels):
                terminal_ok = True
            if not terminal_ok:
                if last_state == "CLOSED":
                    return DispatchResult(False, reason="previous issue not terminal")
                if _serial_concurrency():
                    return DispatchResult(False, reason="previous issue not terminal")

        # AC-2: re-register impl-phase running Issues missing from in_flight before
        # orphan detection in _dispatch_pipeline_ready can halt the whole queue.
        recovered = _recover_untracked_in_flight(client, store, snap)
        if recovered:
            snap = store.snapshot()

        if not _dispatch_pipeline_ready(snap, idle_minutes, now):
            return DispatchResult(False, reason="pipeline not idle")

        concurrency = get_config().concurrency
        role_map = _required_engines()
        counts = in_flight_by_engine(snap.in_flight, role_engine_map=role_map)
        last_paused_reason: str | None = None

        for rid in snap.active_order:
            req = store.effective_request(snap, rid)
            if req is None:
                continue
            if not _source_in_window(req.source, req.actor_kind, now, start, end):
                continue
            if snap.halt:
                halt_scope = snap.halt_scope
                if halt_scope.startswith("phase:") and req.phase == halt_scope[len("phase:"):]:
                    continue
                elif halt_scope.startswith("repo:"):
                    meta = snap.request_meta.get(rid) or {}
                    if meta.get("target_repo", "") == halt_scope[len("repo:"):]:
                        continue
            engine = _resolve_engine(req.phase)
            role = _phase_role_map()[req.phase]
            paused_for_role = _required_engines_paused(role=role)
            if paused_for_role:
                last_paused_reason = (
                    f"required engine paused: {', '.join(paused_for_role)} (role={role})"
                )
                continue
            if counts.get(engine, 0) >= concurrency.limit(engine):
                if concurrency.strict_order:
                    break
                continue
            try:
                issue = client.issue_get(
                    req.issue, fields=["state", "labels", "title", "body", "number"]
                )
            except Exception:
                continue
            decision = deterministic_decision(
                req,
                issue,
                open_issues=open_issues,
                force=store.is_force(snap, rid),
            )
            if decision.kind == "repair":
                _apply_repair(store, client, rid, req, issue, decision.reason)
                continue
            if decision.kind != "keep":
                _apply_terminal(
                    store,
                    client,
                    rid,
                    req.issue,
                    decision.kind,
                    decision.reason,
                    close_issue=decision.close_issue,
                    add_rejected_label=decision.add_rejected_label,
                    comment=decision.comment,
                )
                continue
            ok, _why = phase_preconditions(req.phase, issue, client, req.issue)
            if not ok:
                continue

            after_issues = list((snap.request_meta.get(rid) or {}).get("after") or [])
            if after_issues:
                after_result = check_dependencies(after_issues, client=client)
                if after_result.decision == "BLOCK":
                    continue

            impl_phase = _primary_impl_phase_name()
            if impl_phase and req.phase == impl_phase:
                # P0 requires: re-check deletion referrers on the dispatch-time base (#3953).
                refs = deletion_references_for_body(str(issue.get("body") or ""))
                if refs:
                    lang = get_config().language
                    references = format_deletion_references(
                        refs, lang.message("queue.deletion_refs_lead_dispatch")
                    )
                    _ensure_comment(
                        client,
                        req.issue,
                        rid,
                        "scope_coupling_blocked",
                        lang.message("queue.deletion_refs_before_dispatch", references=references),
                    )
                    continue

            candidate_repo, candidate_paths = _issue_target_meta(issue)
            conflict = _allow_paths_conflict(
                candidate_repo, candidate_paths, snap.in_flight, candidate_phase=req.phase
            )
            if conflict is not None:
                if concurrency.strict_order:
                    break
                continue

            label = READY_LABEL[req.phase]
            labels = label_names(issue)
            if label not in labels:
                client.issue_update(req.issue, labels_add=[label], labels_remove=[])
            handler = _phase_handler_map().get(req.phase)
            redispatch_note = ""
            if handler and _handler_key_consumed(handler, req.issue):
                # A label alone makes the watcher skip on the idempotency key; bump the generation.
                rc = _trigger_ghdag_redispatch(
                    req.issue, handler, reason=f"queue request {rid} ({req.source})"
                )
                redispatch_note = (
                    get_config().language.message("queue.redispatch_ok")
                    if rc == 0
                    else get_config().language.message(
                        "queue.redispatch_failed", rc=rc, issue=req.issue, handler=handler
                    )
                )
            _ensure_comment(
                client,
                req.issue,
                rid,
                "dispatched",
                get_config().language.message(
                    "queue.dispatched", label=label, request_id=rid, note=redispatch_note
                ),
            )
            store.add_in_flight(
                req.issue,
                engine,
                role=role,
                phase=req.phase,
                allow_paths=candidate_paths,
                target_repo=candidate_repo or None,
            )
            store.complete(rid, "dispatched", extra_meta={"label": label})
            store.set_last_issue(req.issue)
            return DispatchResult(
                True, issue=req.issue, label=label, request_id=rid, reason="dispatched"
            )

        if last_paused_reason:
            return DispatchResult(False, reason=last_paused_reason)

    return DispatchResult(False, reason="no dispatchable request")


def _cmd_enqueue(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if args.queue_path else None,
        state_path=Path(args.state_path) if args.state_path else None,
        lock_path=Path(args.lock_path) if args.lock_path else None,
    )
    try:
        result = store.enqueue(
            issue=args.issue,
            phase=args.phase,
            source=args.source,
            actor_kind=args.actor_kind,
            priority=args.priority,
            requested_by=[args.requested_by],
            force=bool(args.force),
            after=getattr(args, "after", None) or None,
        )
    except QueueValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "request_id": result.request_id,
                "created": result.created,
                "merged": result.merged,
                "message": result.message,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_tick(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    result = dispatch_one(store=store, seed_path=Path(args.seed) if getattr(args, "seed", None) else None)
    if result.dispatched:
        print(f"dispatched #{result.issue} {result.label} request={result.request_id}")
    else:
        print(f"no dispatch: {result.reason}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    snap = store.snapshot()
    print(
        "issuesmith-queue status: "
        f"revision={snap.revision} "
        f"active={len(snap.active_order)} "
        f"halt={snap.halt} "
        f"last_issue={snap.last_issue}"
    )
    print(f"  in_flight: {_format_in_flight_status(snap)}")
    for entry in snap.in_flight:
        issue_num = entry.get("issue")
        engine = entry.get("engine", "")
        role = entry.get("role")
        role_part = f" role={role}" if role else ""
        held = entry.get("held")
        held_part = f" held=andon:{held.get('by', '')}" if isinstance(held, dict) and held else ""
        print(f"  - in_flight issue=#{issue_num} engine={engine}{role_part}{held_part}")
    if snap.halt and snap.halt_reason:
        print(f"  halt_reason: {snap.halt_reason}")
    client: ForgePort | None = None
    try:
        client = get_forge(repo=REPO)
    except Exception:
        client = None
    if snap.last_issue is not None and client is not None:
        try:
            last = client.issue_get(snap.last_issue, fields=["state", "labels"])
            last_labels = label_names(last)
            last_state = str(last.get("state", "")).upper()
            terminal = set(get_config().terminal_labels)
            terminal_ok = last_state == "CLOSED" and (
                bool(last_labels & terminal)
                or bool(last_labels & get_terminal_without_merge())
            )
            if not terminal_ok and milestone_last_issue_terminal_ok(last_labels):
                terminal_ok = True
            if last_state == "CLOSED" and not terminal_ok:
                print(
                    f"  warning: last_issue #{snap.last_issue} is CLOSED without terminal label "
                    f"(labels: {sorted(last_labels)})"
                )
        except Exception as exc:
            print(f"  warning: last_issue #{snap.last_issue} fetch failed: {exc}", file=sys.stderr)
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if not req:
            continue
        role = _phase_role_map().get(req.phase, "")
        try:
            engine = _resolve_engine(req.phase)
        except Exception:
            engine = "?"
        print(
            f"  - {rid[:8]}… issue=#{req.issue} phase={req.phase} "
            f"engine={engine} role={role} priority={req.priority} source={req.source}"
        )
        if role:
            paused_for_role = _required_engines_paused(role=role)
            if paused_for_role:
                print(
                    f"    waiting: required engine paused: "
                    f"{', '.join(paused_for_role)} (role={role})"
                )
        meta = snap.request_meta.get(rid) or {}
        after_issues = list(meta.get("after") or [])
        if after_issues and client is not None:
            try:
                after_result = check_dependencies(after_issues, client=client)
                blocking_after = [s.issue for s in after_result.blocking_deps]
            except Exception:
                blocking_after = after_issues
            if blocking_after:
                nums = ", ".join(f"#{n}" for n in blocking_after)
                print(f"    blocked_on: [{nums}]")
        elif after_issues:
            nums = ", ".join(f"#{n}" for n in after_issues)
            print(f"    blocked_on: [{nums}]")
        if client is None:
            continue
        try:
            issue = client.issue_get(
                req.issue, fields=["state", "labels", "body", "number"]
            )
        except Exception:
            continue
        candidate_repo, candidate_paths = _issue_target_meta(issue)
        conflict = _allow_paths_conflict(
            candidate_repo, candidate_paths, snap.in_flight, candidate_phase=req.phase
        )
        if conflict is None:
            continue
        conflict_entry = next(
            (e for e in snap.in_flight if e.get("issue") == conflict),
            {},
        )
        overlap = _conflict_overlap_path(candidate_paths, conflict_entry)
        print(
            f"    waiting: #{req.issue} (conflict with #{conflict} on {overlap})"
        )
    if client is not None:
        try:
            untracked = _find_untracked_running(client, snap)
            if untracked:
                nums = ", ".join(f"#{n}" for n in sorted(untracked))
                print(f"  warning: untracked running issues: {nums}")
        except Exception as exc:
            print(f"  warning: untracked check failed: {exc}", file=sys.stderr)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report queue health; flags orphan active_order ids and untracked running Issues."""
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    # Raw state may retain request_ids missing from the JSONL (snapshot filters them).
    state_path = store.state_path
    orphan_ids: list[str] = []
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            with store.lock():
                requests = store._read_requests_unlocked()
            for rid in raw.get("active_order") or []:
                if isinstance(rid, str) and rid not in requests:
                    orphan_ids.append(rid)
    if orphan_ids:
        print(f"orphan active_order ids: {', '.join(orphan_ids)}")
        return 1

    snap = store.snapshot()
    try:
        client = get_forge(repo=REPO)
    except Exception as exc:
        print(f"error: forge client unavailable: {exc}", file=sys.stderr)
        return 1
    try:
        untracked = _find_untracked_running(client, snap)
    except Exception as exc:
        print(f"error: untracked check failed: {exc}", file=sys.stderr)
        return 1
    if untracked:
        nums = ", ".join(f"#{n}" for n in sorted(untracked))
        print(f"untracked running issues: {nums}")
        return 1

    dispatch_blocked = 0
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        after_issues = list((snap.request_meta.get(rid) or {}).get("after") or [])
        if not after_issues:
            continue
        try:
            after_result = check_dependencies(after_issues, client=client)
            if after_result.decision == "BLOCK":
                dispatch_blocked += 1
        except Exception:
            pass
    if dispatch_blocked:
        print(f"dispatch_blocked: {dispatch_blocked} requests")
    print("doctor ok: no untracked running issues")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    keep_last = bool(getattr(args, "keep_last_issue", False))
    store.clear_halt()
    if keep_last:
        snap = store.snapshot()
        print(f"issuesmith-queue reset: halt=false last_issue={snap.last_issue} (kept)")
    else:
        store.set_last_issue(None)
        print("issuesmith-queue reset: halt=false last_issue=None")
    return 0


def _cmd_skip(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    issue = int(args.issue)
    reason = str(args.reason) if args.reason else ""
    snap = store.snapshot()
    if snap.last_issue is None:
        print("error: no last_issue to skip", file=sys.stderr)
        return 1
    if snap.last_issue != issue:
        print(
            f"error: last_issue is #{snap.last_issue}, not #{issue}",
            file=sys.stderr,
        )
        return 1

    store.set_last_issue(None)
    if snap.halt:
        store.clear_halt()

    client = get_forge(repo=REPO)
    try:
        client.issue_get(issue, fields=["number", "state"])
    except GitHubApiError as exc:
        if exc.status_code == 404:
            print(
                f"skip: skipped comment for #{issue} (404 not found)",
                file=sys.stderr,
            )
        else:
            print(
                f"warning: issue_get #{issue} failed during skip: {exc}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"warning: issue_get #{issue} failed during skip: {exc}",
            file=sys.stderr,
        )
    else:
        body = get_config().language.message("queue.skipped", issue=issue, reason=reason)
        try:
            client.issue_comment(issue, body)
        except Exception as exc:
            print(f"warning: failed to comment on #{issue}: {exc}", file=sys.stderr)

    print(json.dumps({"issue": issue, "outcome": "skipped", "reason": reason}, ensure_ascii=False))
    return 0


def _cmd_dequeue(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    request_id = str(args.request_id)
    reason = str(args.reason) if args.reason else ""
    snap = store.snapshot()
    if request_id not in snap.active_order:
        print(f"error: request {request_id} is not in active_order", file=sys.stderr)
        return 1

    req = store.effective_request(snap, request_id)
    store.complete(request_id, "dequeued", extra_meta={"reason": reason})

    if req is not None:
        client = get_forge(repo=REPO)
        try:
            client.issue_get(req.issue, fields=["number", "state"])
        except GitHubApiError as exc:
            if exc.status_code == 404:
                print(
                    f"dequeue: skipped comment for #{req.issue} (404 not found)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"warning: issue_get #{req.issue} failed during dequeue: {exc}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"warning: issue_get #{req.issue} failed during dequeue: {exc}",
                file=sys.stderr,
            )
        else:
            body = get_config().language.message(
                "queue.dequeued", request_id=request_id, reason=reason
            )
            try:
                client.issue_comment(req.issue, body)
            except Exception as exc:
                print(f"warning: failed to comment on #{req.issue}: {exc}", file=sys.stderr)

    print(json.dumps({"request_id": request_id, "outcome": "dequeued", "reason": reason}, ensure_ascii=False))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    purged = store.purge_orphan_ids()
    if purged:
        print(f"purged orphan active_order ids: {', '.join(purged)}")

    snap = store.snapshot()
    active_ids = set(snap.active_order)
    completed = set(snap.completed_request_ids)
    errors: list[str] = []
    if active_ids & completed:
        errors.append("active_order intersects completed_request_ids")
    # Same issue+phase uniqueness
    seen: dict[tuple[int, str], str] = {}
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        key = (req.issue, req.phase)
        if key in seen:
            errors.append(f"duplicate active {key}")
        seen[key] = rid
    if snap.last_triaged_revision > snap.revision:
        errors.append("last_triaged_revision > revision")

    offline = bool(getattr(args, "offline", False))
    if not offline:
        client = get_forge(repo=REPO)
        in_flight: set[int] = set()
        labels_to_check: list[str] = []
        for ph in get_config().phases:
            if (
                ph.role == "design"
                or ph.writes_files
                or "closing_pr_exists" in ph.advance_when
            ):
                ready, running, _ = get_config().phase_labels(ph.name)
                labels_to_check.extend([ready, running])
        for label in labels_to_check:
            try:
                issues = client.list_issues(label, state="open")
            except Exception as exc:
                print(f"warning: list_issues({label}) failed: {exc}", file=sys.stderr)
                continue
            if not isinstance(issues, list):
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                num = issue.get("number")
                if isinstance(num, int):
                    in_flight.add(num)
        if len(in_flight) >= 2:
            nums = ", ".join(f"#{n}" for n in sorted(in_flight))
            errors.append(f"multiple issues in-flight: {nums}")

    if errors:
        for e in errors:
            print(f"AUDIT FAIL: {e}")
        return 1
    print("AUDIT OK")
    return 0


def _cmd_triage_log(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else DEFAULT_TRIAGE_LOG_PATH
    if not path.exists():
        print("no triage log")
        return 0
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    last_n = lines[-args.last :]
    for ln in last_n:
        print(ln)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    night_state_path = Path(args.from_night_queue_state)
    seed_path = Path(args.seed)
    dry_run = bool(args.dry_run)

    planned: list[dict[str, Any]] = []
    if night_state_path.exists():
        try:
            night = json.loads(night_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            night = {}
        if isinstance(night, dict):
            planned.append(
                {
                    "action": "import_night_state",
                    "last_issue": night.get("last_issue"),
                    "halt": night.get("halt"),
                    "halt_reason": night.get("halt_reason"),
                }
            )

    for entry in load_seed_entries(seed_path):
        planned.append({"action": "enqueue_seed", **entry})

    if dry_run:
        print(json.dumps({"planned": planned}, ensure_ascii=False, indent=2))
        return 0

    if night_state_path.exists():
        try:
            night = json.loads(night_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            night = {}
        if isinstance(night, dict):
            if night.get("last_issue") is not None:
                store.set_last_issue(night.get("last_issue"))
            if night.get("halt") is True:
                store.set_halt(True, night.get("halt_reason"), event="night_seed")

    created = ensure_seeds_enqueued(store, seed_path=seed_path)
    snap = store.snapshot()
    print(
        json.dumps(
            {
                "created": created,
                "active": len(snap.active_order),
                "last_issue": snap.last_issue,
                "halt": snap.halt,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_release(args: argparse.Namespace) -> int:
    from issuesmith.observe.dag_state import load_dag_states

    store = QueueStore(
        queue_path=Path(args.queue_path) if args.queue_path else None,
        state_path=Path(args.state_path) if args.state_path else None,
        lock_path=Path(args.lock_path) if args.lock_path else None,
    )
    issue = int(args.issue)
    snap = store.snapshot()
    entry = next(
        (e for e in snap.in_flight if isinstance(e, dict) and e.get("issue") == issue),
        None,
    )
    if entry is None:
        print(f"error: no in_flight entry for issue #{issue}", file=sys.stderr)
        return 1
    if entry.get("role") != "design":
        print(
            f"error: issue #{issue} is not a design-slot in_flight entry; cannot release",
            file=sys.stderr,
        )
        return 1
    dag_states = load_dag_states(EXEC_PATH, DONE_DIR, DONE_DIR.parent / "running")
    state = dag_states.get(issue)
    if state is not None and state.status == "running":
        print(
            f"error: issue #{issue} DAG is currently running; cannot release in_flight",
            file=sys.stderr,
        )
        return 1
    store.remove_in_flight(issue)
    role = entry.get("role", "unknown")
    dispatched_at = entry.get("dispatched_at", "unknown")
    print(f"released in_flight #{issue} (role={role}, dispatched_at={dispatched_at})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issuesmith.queue")
    parser.add_argument("--queue-path", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--lock-path", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_enq = sub.add_parser("enqueue")
    p_enq.add_argument("--issue", type=int, required=True)
    p_enq.add_argument(
        "--phase",
        required=True,
        choices=[p.name for p in _configured_phases()],
    )
    p_enq.add_argument("--source", required=True)
    p_enq.add_argument("--actor-kind", required=True, choices=["human", "automation"])
    p_enq.add_argument("--priority", required=True, choices=["high", "normal", "low"])
    p_enq.add_argument("--requested-by", required=True)
    p_enq.add_argument("--force", action="store_true")
    p_enq.add_argument("--after", type=int, action="append", default=None)
    p_enq.set_defaults(func=_cmd_enqueue)

    p_tick = sub.add_parser("tick")
    p_tick.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    p_tick.set_defaults(func=_cmd_tick)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=_cmd_status)

    p_doctor = sub.add_parser(
        "doctor",
        help="Check for untracked in-flight Issues (queue health)",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_reset = sub.add_parser("reset")
    p_reset.add_argument(
        "--keep-last-issue",
        action="store_true",
        help="Clear halt only; keep last_issue (legacy behavior)",
    )
    p_reset.set_defaults(func=_cmd_reset)

    p_skip = sub.add_parser("skip")
    p_skip.add_argument("--issue", type=int, required=True)
    p_skip.add_argument("--reason", default="")
    p_skip.set_defaults(func=_cmd_skip)

    p_deq = sub.add_parser("dequeue")
    p_deq.add_argument("--request-id", required=True)
    p_deq.add_argument("--reason", default="")
    p_deq.set_defaults(func=_cmd_dequeue)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument(
        "--offline",
        action="store_true",
        help="Skip GitHub in-flight checks; local store integrity only",
    )
    p_audit.set_defaults(func=_cmd_audit)

    p_tlog = sub.add_parser("triage-log")
    p_tlog.add_argument("--last", type=int, default=20)
    p_tlog.add_argument("--path", default=None)
    p_tlog.set_defaults(func=_cmd_triage_log)

    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--from-night-queue-state", default=str(DEFAULT_NIGHT_STATE_PATH))
    p_mig.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.set_defaults(func=_cmd_migrate)

    p_release = sub.add_parser(
        "release",
        help="Explicitly release a design-slot in_flight entry (recovery only)",
    )
    p_release.add_argument("--issue", type=int, required=True)
    p_release.set_defaults(func=_cmd_release)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

def issue_target_meta(issue: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Public wrapper of ``_issue_target_meta`` for resume / dispatch callers."""
    return _issue_target_meta(issue)


def resolve_engine(phase: str) -> str:
    """Public wrapper of ``_resolve_engine``: engine currently assigned to ``phase``'s role."""
    return _resolve_engine(phase)


def _register_precondition_predicates() -> None:
    from issuesmith.pins import develop_pins_landed
    from issuesmith.preconditions import PreconditionContext, register

    def _pins_landed(ctx: PreconditionContext, config) -> tuple[bool, str]:
        body = str(ctx.issue.get("body") or "")
        return develop_pins_landed(body, config)

    register("pins_landed", _pins_landed)


_register_precondition_predicates()
