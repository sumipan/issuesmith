"""Lane ledger planning: capacity, candidates, enqueue, and patrol report (#4792)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import yaml

if TYPE_CHECKING:
    from issuesmith.queue import QueuePlan

from issuesmith.config import ConfigError, IssuesmithConfig
from issuesmith.projection import state_from_labels
from issuesmith.queue_store import QueueSnapshot, in_flight_by_engine


@dataclass
class Ledger:
    lanes: dict[str, list[int]]
    hold: set[int] = field(default_factory=set)
    backlog: list[int] = field(default_factory=list)
    queued_stale_minutes: int = 30
    api_min_remaining: int = 800
    report_every_minutes: int = 120
    report_since: str = ""
    high: set[int] = field(default_factory=set)
    high_labels: tuple[str, ...] = ()
    workflows: dict[str, list[int]] = field(default_factory=dict)
    default_workflow: str = "issuesmith"


@dataclass
class LanePlan:
    slots: dict[str, dict[str, int]]
    candidates: list[dict[str, Any]] = field(default_factory=list)
    enqueue: list[dict[str, Any]] = field(default_factory=list)
    escalations: list[dict[str, Any]] = field(default_factory=list)
    waits: list[dict[str, Any]] = field(default_factory=list)
    halt: bool = False


@dataclass(frozen=True)
class IssueView:
    number: int
    labels: tuple[str, ...]
    state: str = "open"


def _normalize_workflow_issue_list(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    seen: set[int] = set()
    out: list[int] = []
    for item in values:
        if isinstance(item, bool):
            continue
        try:
            number = int(item)
        except (TypeError, ValueError):
            continue
        if number <= 0 or number in seen:
            continue
        seen.add(number)
        out.append(number)
    return out


def _load_workflows(raw: Mapping[str, Any]) -> dict[str, list[int]]:
    workflows_raw = raw.get("workflows") or {}
    if not isinstance(workflows_raw, dict):
        return {}
    return {
        str(name): _normalize_workflow_issue_list(nums)
        for name, nums in workflows_raw.items()
        if isinstance(name, str) and isinstance(nums, list)
    }


def _validate_workflow_ownership(default_workflow: str, workflows: dict[str, list[int]]) -> None:
    if default_workflow in workflows:
        raise ConfigError(
            "lanes ledger workflows: "
            f"key {default_workflow!r} conflicts with default_workflow"
        )
    owner: dict[int, str] = {}
    for workflow, numbers in workflows.items():
        for number in numbers:
            prior = owner.get(number)
            if prior is not None:
                raise ConfigError(
                    "lanes ledger workflows: "
                    f"issue #{number} is listed in both {prior!r} and {workflow!r}"
                )
            owner[number] = workflow


def select_workflow(ledger: Ledger, namespace: str) -> Ledger:
    if not ledger.workflows:
        return ledger

    if namespace == ledger.default_workflow:
        excluded = {n for nums in ledger.workflows.values() for n in nums}

        def keep(number: int) -> bool:
            return number not in excluded

    elif namespace in ledger.workflows:
        allowed = set(ledger.workflows[namespace])

        def keep(number: int) -> bool:
            return number in allowed

    else:

        def keep(_number: int) -> bool:
            return False

    def filter_list(numbers: list[int]) -> list[int]:
        return [n for n in numbers if keep(n)]

    return replace(
        ledger,
        lanes={name: filter_list(nums) for name, nums in ledger.lanes.items()},
        hold={n for n in ledger.hold if keep(n)},
        backlog=[n for n in ledger.backlog if keep(n)],
        high={n for n in ledger.high if keep(n)},
    )


def load_ledger(path: Path, *, default_api_min: int = 800) -> Ledger:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"lanes ledger must be a mapping: {path}")
    lanes_raw = raw.get("lanes") or {}
    lanes = {
        str(name): [int(x) for x in (nums or [])]
        for name, nums in lanes_raw.items()
        if isinstance(nums, list)
    }
    default_workflow = str(raw.get("default_workflow") or "").strip() or "issuesmith"
    workflows = _load_workflows(raw)
    _validate_workflow_ownership(default_workflow, workflows)
    return Ledger(
        lanes=lanes,
        hold={int(x) for x in (raw.get("hold") or [])},
        backlog=[int(x) for x in (raw.get("backlog") or [])],
        queued_stale_minutes=int(raw.get("queued_stale_minutes", 30)),
        api_min_remaining=int(raw.get("api_min_remaining", default_api_min)),
        report_every_minutes=int(raw.get("report_every_minutes", 120)),
        report_since=str(raw.get("report_since") or ""),
        high={int(x) for x in (raw.get("high") or [])},
        high_labels=tuple(str(x) for x in (raw.get("high_labels") or [])),
        workflows=workflows,
        default_workflow=default_workflow,
    )


def resolve_report_since(report_since: str, now: datetime, timezone: str) -> str:
    if report_since.strip().lower() != "daily":
        return report_since
    tz = ZoneInfo(timezone)
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat()


def ledger_state_path(ledger_path: Path) -> Path:
    return ledger_path.parent / f"{ledger_path.stem}.state.json"


def load_report_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def save_report_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def count_merge_done_since(client: Any, since_iso: str, config: IssuesmithConfig) -> int | None:
    """Count closed issues whose labels indicate final phase done since ``since_iso``."""
    if not since_iso.strip():
        return None
    try:
        since = datetime.fromisoformat(since_iso)
    except ValueError:
        return None
    merge_done_label = config.phase_labels(config.phases[-1].name)[2]
    try:
        issues = client.list_issues(label=merge_done_label, state="closed")
    except Exception:
        return None
    count = 0
    for item in issues:
        if not isinstance(item, dict):
            continue
        closed_at = str(item.get("closed_at") or "")
        if not closed_at:
            continue
        try:
            closed = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if closed < since.astimezone(closed.tzinfo):
            continue
        labels = [
            str(lab.get("name") if isinstance(lab, dict) else lab)
            for lab in (item.get("labels") or [])
        ]
        status, _ = classify(labels, config, closed=True)
        if status == "done":
            count += 1
    return count


def classify(
    labels: Iterable[str],
    config: IssuesmithConfig,
    *,
    closed: bool = False,
) -> tuple[str, str | None]:
    """Return (status, phase). status: done | busy | blocked | candidate."""
    label_list = list(labels)
    if closed:
        return "done", None
    terminal = set(config.terminal_labels)
    if terminal & set(label_list):
        return "done", None
    st = state_from_labels(label_list, config)
    if st.andon_kinds or st.waiting:
        return "blocked", None
    if st.queued:
        return "busy", None
    if any(status == "running" for status in st.phases.values()):
        return "busy", None
    if config.phases and all(st.phases.get(ph.name) == "done" for ph in config.phases):
        return "done", None
    for ph in config.phases:
        status = st.phases.get(ph.name)
        if status == "done":
            continue
        if status == "running":
            return "busy", None
        return "candidate", ph.name
    if st.phases:
        return "busy", None
    first = config.phases[0].name if config.phases else None
    return "candidate", first


def _engine_for_phase(phase: str, config: IssuesmithConfig, role_engine_map: Mapping[str, str]) -> str:
    ph = config.phase(phase)
    return str(role_engine_map.get(ph.role, "unknown"))


def _parse_requested_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _queued_by_issue(snapshot: QueueSnapshot) -> dict[int, tuple[str, str]]:
    """Map issue number to (phase, requested_at) for active queue requests."""
    out: dict[int, tuple[str, str]] = {}
    for req in snapshot.active_requests:
        out[req.issue] = (req.phase, req.requested_at)
    return out


def _issue_priority(number: int, labels: Iterable[str], ledger: Ledger) -> str:
    if number in ledger.high:
        return "high"
    label_set = set(labels)
    if ledger.high_labels and label_set.intersection(ledger.high_labels):
        return "high"
    return "normal"


def _queue_waits_and_escalations(
    queue_plan: QueuePlan | None,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    if queue_plan is None:
        return [], False, []
    waits = [
        {
            "issue": entry.issue,
            "phase": entry.phase,
            "engine": entry.engine,
            "reason": entry.reason,
            "detail": entry.detail,
        }
        for entry in queue_plan.entries
        if entry.decision == "wait"
    ]
    escalations: list[dict[str, Any]] = []
    if queue_plan.halt:
        halt_detail = ""
        for entry in queue_plan.entries:
            if entry.reason == "halt":
                halt_detail = entry.detail
                break
        escalations.append({"kind": "queue_halt", "summary": halt_detail})
    seen_paused: set[str] = set()
    for entry in queue_plan.entries:
        if entry.decision != "wait" or entry.reason != "engine_paused":
            continue
        engine = str(entry.engine or "")
        if engine in seen_paused:
            continue
        seen_paused.add(engine)
        escalations.append(
            {"kind": "engine_paused", "engine": engine, "summary": entry.detail}
        )
    return waits, queue_plan.halt, escalations


def plan(
    ledger: Ledger,
    snapshot: QueueSnapshot,
    issues: Mapping[int, IssueView],
    config: IssuesmithConfig,
    now: datetime,
    api_low: bool,
    *,
    role_engine_map: Mapping[str, str],
    queue_plan: QueuePlan | None = None,
) -> LanePlan:
    concurrency = config.concurrency
    in_flight_counts = in_flight_by_engine(snapshot.in_flight, role_engine_map=dict(role_engine_map))
    engines = sorted(set(concurrency.per_engine) | set(in_flight_counts) | set(role_engine_map.values()))
    queued_map = _queued_by_issue(snapshot)
    stale_minutes = ledger.queued_stale_minutes

    queued_fresh_by_engine: dict[str, int] = {e: 0 for e in engines}
    stale_escalations: list[dict[str, Any]] = []
    for issue_num, (phase, requested_at) in queued_map.items():
        engine = _engine_for_phase(phase, config, role_engine_map)
        at = _parse_requested_at(requested_at)
        if at is not None and now - at > timedelta(minutes=stale_minutes):
            stale_escalations.append(
                {
                    "kind": "stale_queued",
                    "issue": issue_num,
                    "summary": f"queued since {requested_at}",
                }
            )
            continue
        queued_fresh_by_engine[engine] = queued_fresh_by_engine.get(engine, 0) + 1

    slots: dict[str, dict[str, int]] = {}
    for engine in engines:
        limit = concurrency.limit(engine)
        in_flight = in_flight_counts.get(engine, 0)
        queued_fresh = queued_fresh_by_engine.get(engine, 0)
        free = max(0, limit - in_flight - queued_fresh)
        slots[engine] = {
            "limit": limit,
            "in_flight": in_flight,
            "queued_fresh": queued_fresh,
            "free": free,
        }

    lane_plan = LanePlan(slots=slots, escalations=list(stale_escalations))
    waits, halt, queue_escalations = _queue_waits_and_escalations(queue_plan)
    lane_plan.waits = waits
    lane_plan.halt = halt
    lane_plan.escalations.extend(queue_escalations)
    wait_reason_by_issue = {w["issue"]: w["reason"] for w in waits}
    for esc in lane_plan.escalations:
        if esc.get("kind") != "stale_queued":
            continue
        issue_num = esc.get("issue")
        if issue_num in wait_reason_by_issue:
            esc["reason"] = wait_reason_by_issue[issue_num]

    if api_low:
        lane_plan.escalations.append({"kind": "api_low", "summary": "GitHub API budget low"})
        lane_plan.slots = slots
        return lane_plan

    lanes = {lane: list(nums) for lane, nums in ledger.lanes.items()}
    in_flight_issues = {int(e.get("issue", 0)) for e in snapshot.in_flight}
    occupied = in_flight_issues | set(queued_map)

    enqueue_order: list[tuple[int, int, int, str, int, str, str, str]] = []

    def _issue_view(number: int) -> IssueView | None:
        return issues.get(number)

    lane_items = list(lanes.items())
    backlog_lane_index = len(lane_items)

    for lane_index, (lane_name, lane_issues) in enumerate(lane_items):
        depth = 0
        for number in lane_issues:
            if number in ledger.hold:
                continue
            info = _issue_view(number)
            if info is None:
                continue
            status, issue_phase = classify(
                info.labels, config, closed=info.state.lower() == "closed"
            )
            if status == "done":
                continue
            if status == "blocked":
                lane_plan.escalations.append(
                    {
                        "kind": "lane_blocked",
                        "lane": lane_name,
                        "issue": number,
                        "summary": "blocked",
                    }
                )
                continue
            if status == "busy" or number in occupied:
                continue
            if status == "candidate" and issue_phase is not None:
                priority = _issue_priority(number, info.labels, ledger)
                prio_ord = 0 if priority == "high" else 1
                engine = _engine_for_phase(issue_phase, config, role_engine_map)
                lane_plan.candidates.append(
                    {
                        "lane": lane_name,
                        "issue": number,
                        "phase": issue_phase,
                        "depth": depth,
                        "priority": priority,
                    }
                )
                enqueue_order.append(
                    (prio_ord, depth, lane_index, lane_name, number, issue_phase, engine, priority)
                )
                depth += 1

    for number in ledger.backlog:
        if number in ledger.hold or number in occupied:
            continue
        info = _issue_view(number)
        if info is None:
            continue
        status, issue_phase = classify(
            info.labels, config, closed=info.state.lower() == "closed"
        )
        if status == "candidate" and issue_phase is not None:
            priority = _issue_priority(number, info.labels, ledger)
            prio_ord = 0 if priority == "high" else 1
            eng = _engine_for_phase(issue_phase, config, role_engine_map)
            lane_plan.candidates.append(
                {
                    "lane": "backlog",
                    "issue": number,
                    "phase": issue_phase,
                    "depth": 0,
                    "priority": priority,
                }
            )
            enqueue_order.append(
                (prio_ord, 0, backlog_lane_index, "backlog", number, issue_phase, eng, priority)
            )

    enqueue_order.sort(key=lambda row: (row[0], row[1], row[2]))

    free_total = sum(s["free"] for s in slots.values())
    for _, _, _, lane_name, number, phase, engine, priority in enqueue_order:
        if len(lane_plan.enqueue) >= free_total:
            break
        if slots.get(engine, {}).get("free", 0) <= 0:
            continue
        lane_plan.enqueue.append(
            {
                "issue": number,
                "phase": phase,
                "engine": engine,
                "lane": lane_name,
                "priority": priority,
            }
        )
        slots[engine]["free"] -= 1
        slots[engine]["queued_fresh"] += 1

    lane_plan.slots = slots
    return lane_plan


def apply(plan: LanePlan, store: Any) -> LanePlan:
    for entry in plan.enqueue:
        store.enqueue(
            issue=int(entry["issue"]),
            phase=str(entry["phase"]),
            source="lanes",
            actor_kind="automation",
            priority=str(entry.get("priority", "normal")),
            requested_by=["lanes"],
        )
    return plan


def plan_to_dict(plan: LanePlan) -> dict[str, Any]:
    return {
        "slots": plan.slots,
        "candidates": plan.candidates,
        "enqueue": plan.enqueue,
        "escalations": plan.escalations,
        "waits": plan.waits,
        "halt": plan.halt,
    }


def _labels_from_raw(raw: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(lab.get("name") if isinstance(lab, dict) else lab)
        for lab in (raw.get("labels") or [])
    )


def _issue_view_from_raw(number: int, raw: Mapping[str, Any]) -> IssueView:
    return IssueView(
        number=number,
        labels=_labels_from_raw(raw),
        state=str(raw.get("state") or "open"),
    )


def fetch_issue_views(
    client: Any,
    numbers: Iterable[int],
    *,
    cache: Mapping[int, Mapping[str, Any]] | None = None,
) -> dict[int, IssueView]:
    """Load issue labels and state for ledger Issue numbers."""
    out: dict[int, IssueView] = {}
    for number in sorted({int(n) for n in numbers}):
        cached = (cache or {}).get(number)
        if isinstance(cached, Mapping):
            out[number] = IssueView(
                number=number,
                labels=_labels_from_raw(cached),
                state=str(cached.get("state") or "open"),
            )
            continue
        try:
            raw = client.issue_get(number, fields=["number", "state", "labels"])
        except Exception:
            continue
        if not isinstance(raw, Mapping):
            continue
        out[number] = _issue_view_from_raw(number, raw)
    return out


@dataclass
class LaneCheck:
    plan: LanePlan
    lane_status: dict[str, str]
    in_flight: list[int]
    held: list[int]
    queued: list[int]
    queued_reasons: dict[int, str]
    api_remaining: int | None
    api_low: bool
    generated_at: str

    @property
    def has_work(self) -> bool:
        return bool(self.plan.enqueue or self.plan.escalations or self.api_low)


def _snapshot_issue_views(snapshot: QueueSnapshot) -> tuple[list[int], list[int], list[int]]:
    in_flight: list[int] = []
    held: list[int] = []
    for entry in snapshot.in_flight:
        if not isinstance(entry, dict) or not isinstance(entry.get("issue"), int):
            continue
        number = int(entry["issue"])
        if entry.get("held"):
            held.append(number)
        else:
            in_flight.append(number)
    queued = [int(req.issue) for req in snapshot.active_requests]
    return sorted(set(in_flight)), sorted(set(held)), sorted(set(queued))


def _lane_status_for_ledger(
    ledger: Ledger,
    issues: Mapping[int, IssueView],
    occupied: set[int],
    config: IssuesmithConfig,
) -> dict[str, str]:
    out: dict[str, str] = {}
    for lane, numbers in ledger.lanes.items():
        head: str | None = None
        for number in numbers:
            if number in ledger.hold:
                head = head or f"hold #{number}"
                continue
            if number in occupied:
                head = head or f"busy #{number}"
                continue
            info = issues.get(number)
            if info is None:
                head = head or f"unknown #{number}"
                continue
            status, phase = classify(
                info.labels, config, closed=info.state.lower() == "closed"
            )
            if status == "done":
                continue
            if status == "blocked":
                head = head or f"blocked #{number}"
                continue
            if status == "candidate" and phase is not None:
                head = head or f"ready #{number} ({phase})"
                continue
            head = head or f"{status} #{number}"
        out[lane] = head or "empty"
    return out


def check(
    ledger: Ledger,
    snapshot: QueueSnapshot,
    issues: Mapping[int, IssueView],
    config: IssuesmithConfig,
    now: datetime,
    *,
    role_engine_map: Mapping[str, str],
    queue_plan: QueuePlan | None = None,
    api_remaining: int | None = None,
    api_low: bool = False,
) -> LaneCheck:
    lane_plan = plan(
        ledger,
        snapshot,
        issues,
        config,
        now,
        api_low,
        role_engine_map=role_engine_map,
        queue_plan=queue_plan,
    )
    in_flight, held, queued = _snapshot_issue_views(snapshot)
    occupied = set(in_flight) | set(held) | set(queued)
    queued_reasons: dict[int, str] = {}
    if queue_plan is not None:
        queued_set = set(queued)
        for entry in queue_plan.entries:
            if entry.issue in queued_set:
                queued_reasons[entry.issue] = str(entry.detail or "")
    generated_at = now.astimezone(ZoneInfo(config.timezone)).isoformat()
    return LaneCheck(
        plan=lane_plan,
        lane_status=_lane_status_for_ledger(ledger, issues, occupied, config),
        in_flight=in_flight,
        held=held,
        queued=queued,
        queued_reasons=queued_reasons,
        api_remaining=api_remaining,
        api_low=api_low,
        generated_at=generated_at,
    )


def check_to_dict(result: LaneCheck) -> dict[str, Any]:
    payload = plan_to_dict(result.plan)
    payload.update(
        {
            "lane_status": result.lane_status,
            "in_flight": result.in_flight,
            "held": result.held,
            "queued": result.queued,
            "queued_reasons": {str(k): v for k, v in result.queued_reasons.items()},
            "api_remaining": result.api_remaining,
            "api_low": result.api_low,
            "has_work": result.has_work,
            "generated_at": result.generated_at,
        }
    )
    return payload


def report(
    plan: LanePlan,
    auto_answer_plan: Any,
    merged_count: int | None,
    state: dict[str, Any],
    now: datetime,
    ledger: Ledger,
) -> tuple[dict[str, Any], str]:
    escalations = list(plan.escalations)
    escalations.extend(getattr(auto_answer_plan, "escalations", []) or [])

    prev_enqueue = state.get("enqueue")
    prev_escalations = state.get("escalations")
    changed = prev_enqueue != plan.enqueue or prev_escalations != escalations

    quiet = plan.halt or any(w.get("reason") == "engine_paused" for w in plan.waits)

    report_due = changed
    last_raw = state.get("last_report_at")
    if not report_due and last_raw:
        try:
            last = datetime.fromisoformat(str(last_raw))
            if now - last >= timedelta(minutes=ledger.report_every_minutes):
                report_due = not quiet
        except ValueError:
            report_due = not quiet

    merge_done_since = merged_count if ledger.report_since.strip() else None

    slot_parts: list[str] = []
    for engine, slot in sorted(plan.slots.items()):
        slot_parts.append(
            f"{engine} {slot['in_flight']}/{slot['limit']} free {slot['free']}"
        )
    esc_n = len(escalations)
    summary = (
        f"slots {', '.join(slot_parts)}, {esc_n} escalations"
        + (
            f", done {merge_done_since} since {ledger.report_since}"
            if merge_done_since is not None
            else ""
        )
    )

    auto_answers = getattr(auto_answer_plan, "answers", None) or []

    payload: dict[str, Any] = {
        "slots": plan.slots,
        "escalations": escalations,
        "merge_done_since": merge_done_since,
        "report_due": report_due,
        "enqueue": plan.enqueue,
        "candidates": plan.candidates,
        "waits": plan.waits,
        "halt": plan.halt,
        "quiet": quiet,
        "auto_answers": auto_answers,
    }
    return payload, summary
