"""Lane ledger planning: capacity, candidates, enqueue, and patrol report (#4792)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import yaml

from issuesmith.config import ConfigError, IssuesmithConfig
from issuesmith.projection import state_from_labels
from issuesmith.queue_store import QueueSnapshot, in_flight_by_engine


@dataclass
class Ledger:
    lanes: dict[str, list[int]]
    hold: set[int] = field(default_factory=set)
    backlog: list[int] = field(default_factory=list)
    auto_lanes_enabled: bool = False
    queued_stale_minutes: int = 30
    api_min_remaining: int = 800
    report_every_minutes: int = 120
    report_since: str = ""


@dataclass
class LanePlan:
    slots: dict[str, dict[str, int]]
    candidates: list[dict[str, Any]] = field(default_factory=list)
    enqueue: list[dict[str, Any]] = field(default_factory=list)
    escalations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class IssueView:
    number: int
    labels: tuple[str, ...]
    state: str = "open"
    target_repo: str = ""


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
    auto_lanes = raw.get("auto_lanes")
    auto_enabled = False
    if isinstance(auto_lanes, dict):
        auto_enabled = bool(auto_lanes.get("enabled", False))
    return Ledger(
        lanes=lanes,
        hold={int(x) for x in (raw.get("hold") or [])},
        backlog=[int(x) for x in (raw.get("backlog") or [])],
        auto_lanes_enabled=auto_enabled,
        queued_stale_minutes=int(raw.get("queued_stale_minutes", 30)),
        api_min_remaining=int(raw.get("api_min_remaining", default_api_min)),
        report_every_minutes=int(raw.get("report_every_minutes", 120)),
        report_since=str(raw.get("report_since") or ""),
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
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _expand_lanes(
    ledger: Ledger,
    issues: Mapping[int, IssueView],
    known: set[int],
) -> dict[str, list[int]]:
    if not ledger.auto_lanes_enabled:
        return dict(ledger.lanes)
    lanes = {k: list(v) for k, v in ledger.lanes.items()}
    for number, info in sorted(issues.items(), key=lambda t: t[0]):
        if number in known:
            continue
        if info.state.lower() == "closed":
            continue
        repo = info.target_repo
        if not repo:
            continue
        lane = f"auto-{repo.split('/')[-1]}"
        lanes.setdefault(lane, []).append(number)
    return lanes


def plan(
    ledger: Ledger,
    snapshot: QueueSnapshot,
    issues: Mapping[int, IssueView],
    config: IssuesmithConfig,
    now: datetime,
    api_low: bool,
    *,
    role_engine_map: Mapping[str, str],
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
    if api_low:
        lane_plan.escalations.append({"kind": "api_low", "summary": "GitHub API budget low"})
        return lane_plan

    known = {n for lane in ledger.lanes.values() for n in lane} | ledger.hold | set(ledger.backlog)
    lanes = _expand_lanes(ledger, issues, known)
    in_flight_issues = {int(e.get("issue", 0)) for e in snapshot.in_flight}
    occupied = in_flight_issues | set(queued_map)

    round_robin: list[tuple[str, int, str, str]] = []

    def _issue_view(number: int) -> IssueView | None:
        return issues.get(number)

    for lane_name, lane_issues in lanes.items():
        blocked_lane = False
        for number in lane_issues:
            if number in ledger.hold:
                continue
            info = _issue_view(number)
            if info is None:
                continue
            status, phase = classify(info.labels, config, closed=info.state.lower() == "closed")
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
                blocked_lane = True
                break
            if status == "busy":
                break
            if status == "candidate" and phase:
                if number in occupied:
                    break
                lane_plan.candidates.append({"lane": lane_name, "issue": number, "phase": phase})
                round_robin.append((lane_name, number, phase, _engine_for_phase(phase, config, role_engine_map)))
                break
        if blocked_lane:
            continue

    for number in ledger.backlog:
        if number in ledger.hold or number in occupied:
            continue
        info = _issue_view(number)
        if info is None:
            continue
        status, phase = classify(info.labels, config, closed=info.state.lower() == "closed")
        if status == "candidate" and phase:
            eng = _engine_for_phase(phase, config, role_engine_map)
            round_robin.append(("backlog", number, phase, eng))

    free_total = sum(s["free"] for s in slots.values())
    for lane_name, number, phase, engine in round_robin:
        if len(lane_plan.enqueue) >= free_total:
            break
        if slots.get(engine, {}).get("free", 0) <= 0:
            continue
        lane_plan.enqueue.append({"issue": number, "phase": phase, "engine": engine, "lane": lane_name})
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
            priority="normal",
            requested_by=["lanes"],
        )
    return plan


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

    report_due = changed
    last_raw = state.get("last_report_at")
    if not report_due and last_raw:
        try:
            last = datetime.fromisoformat(str(last_raw))
            if now - last >= timedelta(minutes=ledger.report_every_minutes):
                report_due = True
        except ValueError:
            report_due = True

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

    payload: dict[str, Any] = {
        "slots": plan.slots,
        "escalations": escalations,
        "merge_done_since": merge_done_since,
        "report_due": report_due,
        "enqueue": plan.enqueue,
        "candidates": plan.candidates,
    }
    return payload, summary
