"""issuesmith.observe -- pipeline observation layer.

observe() reads queue state, exec records, labels, and metrics to produce
a list of typed events. It has no side effects.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    ChainHaltedEvent,
    IssueStallEvent,
    LabelDriftEvent,
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


def observe(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    *,
    metrics_path: Path | None = None,
    now: datetime | None = None,
) -> list[ObserveEvent]:
    """Collect pipeline events. Read-only; no side effects."""
    if now is None:
        now = datetime.now(timezone.utc)

    obs = config.observe
    events: list[ObserveEvent] = []

    events.extend(_detect_stall(snapshot, config, now, obs.stall_minutes))
    events.extend(_detect_task_timeout(snapshot, now, obs.task_timeout_minutes))
    events.extend(_detect_orphan_exec(snapshot, config))
    events.extend(_detect_label_drift(snapshot, client, config, obs.max_api_calls))
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

    return events


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
) -> list[ObserveEvent]:
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
            events.append(OrphanExecEvent(uuid=uuid_val, issue=issue))

    return events


def _issue_from_key(key: str) -> int | None:
    parts = key.split(":")
    if len(parts) >= 3:
        last = parts[-1]
        if last.isdigit():
            return int(last)
    return None


def _detect_label_drift(
    snapshot: "QueueSnapshot",
    client: "ForgePort",
    config: "IssuesmithConfig",
    max_api_calls: int,
) -> list[ObserveEvent]:
    from issuesmith.ops.labels import (
        ExecRecord,
        _exec_records_from_labels,  # type: ignore[attr-defined]
        _is_managed_label,  # type: ignore[attr-defined]
        _ns,  # type: ignore[attr-defined]
    )
    from issuesmith.ops.labels import (
        project as labels_project,
    )

    events: list[ObserveEvent] = []
    api_calls = 0
    ns = _ns()

    all_issues: set[int] = set()
    for entry in snapshot.in_flight:
        if isinstance(entry, dict) and isinstance(entry.get("issue"), int):
            all_issues.add(entry["issue"])
    for req in snapshot.active_requests:
        all_issues.add(req.issue)

    queued_issues = {req.issue for req in snapshot.active_requests}

    for issue_num in sorted(all_issues):
        if api_calls >= max_api_calls:
            break
        try:
            issue = client.issue_get(issue_num, fields=["labels", "number", "state"])
            api_calls += 1
        except Exception:
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
