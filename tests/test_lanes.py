"""Tests for lanes planning, apply, and report (#4792)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from issuesmith.config import IssuesmithConfig, load_config, reset_config_cache
from issuesmith.lanes import (
    IssueView,
    Ledger,
    apply,
    ledger_state_path,
    load_ledger,
    load_report_state,
    plan,
    plan_to_dict,
    report,
    resolve_report_since,
    save_report_state,
)
from issuesmith.queue import PlanEntry, QueuePlan
from issuesmith.queue_store import QueueRequest, QueueSnapshot

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _cfg(tmp_path: Path) -> IssuesmithConfig:
    path = tmp_path / "issuesmith.yaml"
    path.write_text(yaml.safe_dump({"repo": "org/repo"}), encoding="utf-8")
    reset_config_cache()
    return load_config(path)


def _snapshot(
    *,
    in_flight: list[dict] | None = None,
    active: list[tuple[int, str, str]] | None = None,
) -> QueueSnapshot:
    requests: dict[str, QueueRequest] = {}
    order: list[str] = []
    for idx, (issue, phase, requested_at) in enumerate(active or []):
        rid = f"r{idx}"
        requests[rid] = QueueRequest(
            request_id=rid,
            issue=issue,
            phase=phase,
            source="test",
            actor_kind="automation",
            priority="normal",
            requested_at=requested_at,
            requested_by=("test",),
        )
        order.append(rid)
    return QueueSnapshot(
        schema_version=1,
        revision=0,
        active_order=order,
        completed_request_ids=[],
        last_triaged_revision=0,
        halt=False,
        halt_reason=None,
        requests=requests,
        in_flight=list(in_flight or []),
    )


def test_plan_one_enqueue_from_first_lane(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"a": [1], "b": [2]})
    issues = {
        1: IssueView(1, ()),
        2: IssueView(2, ()),
    }
    snap = _snapshot()
    role_map = {"design": "claude", "implementation": "cursor"}
    lane_plan = plan(ledger, snap, issues, cfg, _T0, False, role_engine_map=role_map)
    assert len(lane_plan.enqueue) == 1
    assert lane_plan.enqueue[0]["issue"] == 1


def test_hold_skipped_lane_blocked(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"lane": [10, 11]}, hold={10})
    issues = {
        11: IssueView(11, ("issuesmith:andon-decision",)),
    }
    snap = _snapshot()
    lane_plan = plan(
        ledger,
        snap,
        issues,
        cfg,
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    assert lane_plan.candidates == []
    assert any(e["kind"] == "lane_blocked" for e in lane_plan.escalations)


def test_stale_queued_and_api_low(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"a": [1]})
    old = (_T0 - timedelta(hours=2)).isoformat()
    snap = _snapshot(active=[(5, "develop", old)])
    issues = {1: IssueView(1, ())}
    lane_plan = plan(
        ledger,
        snap,
        issues,
        cfg,
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    assert any(e["kind"] == "stale_queued" for e in lane_plan.escalations)

    api_plan = plan(
        ledger,
        snap,
        issues,
        cfg,
        _T0,
        True,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    assert api_plan.enqueue == []
    assert any(e["kind"] == "api_low" for e in api_plan.escalations)


def test_apply_calls_enqueue(tmp_path: Path):
    store = MagicMock()
    lane_plan = plan(
        Ledger(lanes={}),
        _snapshot(),
        {},
        _cfg(tmp_path),
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    lane_plan.enqueue = [{"issue": 1, "phase": "draft"}]
    apply(lane_plan, store)
    store.enqueue.assert_called_once()


def test_report_report_due(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={}, report_every_minutes=120)
    lane_plan = plan(
        ledger,
        _snapshot(),
        {},
        cfg,
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    auto = MagicMock(escalations=[])
    payload, _ = report(lane_plan, auto, None, {}, _T0, ledger)
    assert payload["report_due"] is True

    past = _T0 - timedelta(hours=3)
    state = {"last_report_at": past.isoformat(), "enqueue": [], "escalations": []}
    payload2, _ = report(lane_plan, auto, None, state, _T0, ledger)
    assert payload2["report_due"] is True


def test_resolve_report_since_daily(tmp_path: Path):
    cfg = _cfg(tmp_path)
    now = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    resolved = resolve_report_since("daily", now, cfg.timezone)
    assert "00:00:00" in resolved


def test_load_ledger(tmp_path: Path):
    path = tmp_path / "lanes.yaml"
    path.write_text(
        yaml.safe_dump({"lanes": {"main": [1, 2]}, "queued_stale_minutes": 45}),
        encoding="utf-8",
    )
    ledger = load_ledger(path)
    assert ledger.lanes["main"] == [1, 2]
    assert ledger.queued_stale_minutes == 45
    assert ledger_state_path(path).name == "lanes.state.json"


def test_save_report_state_atomic_round_trip(tmp_path: Path):
    state_path = ledger_state_path(tmp_path / "lanes.yaml")
    save_report_state(state_path, {"last_report_at": "2026-10-09T00:00:00+09:00"})
    assert load_report_state(state_path) == {"last_report_at": "2026-10-09T00:00:00+09:00"}
    assert [p.name for p in tmp_path.iterdir()] == [state_path.name]


def _lane_plan(tmp_path: Path, **kwargs):
    defaults = {
        "ledger": Ledger(lanes={}),
        "snapshot": _snapshot(),
        "issues": {},
        "config": _cfg(tmp_path),
        "now": _T0,
        "api_low": False,
        "role_engine_map": {"design": "claude", "implementation": "cursor"},
    }
    defaults.update(kwargs)
    return plan(**defaults)


def test_plan_keeps_scanning_past_blocked_and_busy(tmp_path: Path):
    """Issue #7326: lanes keep scanning past blocked and busy issues."""
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"chain": [101, 102, 103]})
    issues = {
        101: IssueView(101, ("issuesmith:andon-decision",)),
        102: IssueView(102, ()),
        103: IssueView(103, ("issuesmith:develop-running",)),
    }
    lane_plan = _lane_plan(
        tmp_path,
        ledger=ledger,
        issues=issues,
        config=cfg,
    )
    assert any(c["issue"] == 102 for c in lane_plan.candidates)
    assert any(
        e["kind"] == "lane_blocked" and e["issue"] == 101 for e in lane_plan.escalations
    )

    ledger2 = Ledger(lanes={"chain": [201, 202, 204]})
    issues2 = {
        201: IssueView(201, ("issuesmith:develop-running",)),
        202: IssueView(202, ()),
        204: IssueView(204, ()),
    }
    lane_plan2 = _lane_plan(tmp_path, ledger=ledger2, issues=issues2, config=cfg)
    cand_issues = [c["issue"] for c in lane_plan2.candidates]
    assert cand_issues == [202, 204]
    assert [c["depth"] for c in lane_plan2.candidates] == [0, 1]


def test_high_priority_enqueued_first_and_applied_as_high(tmp_path: Path):
    """Issue #7326: ledger high issues enqueue before normal and apply passes priority."""
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"lane": [1, 2]}, high={2})
    issues = {1: IssueView(1, ()), 2: IssueView(2, ())}
    snap = _snapshot()
    role_map = {"design": "claude", "implementation": "cursor"}
    snap = _snapshot()
    lane_plan = plan(
        ledger, snap, issues, cfg, _T0, False, role_engine_map=role_map
    )
    assert len(lane_plan.enqueue) == 1
    assert lane_plan.enqueue[0]["issue"] == 2
    assert lane_plan.enqueue[0]["priority"] == "high"

    store = MagicMock()
    apply(lane_plan, store)
    store.enqueue.assert_called_once()
    assert store.enqueue.call_args.kwargs["priority"] == "high"


def test_plan_carries_queue_wait_reasons(tmp_path: Path):
    """Issue #7326: queue wait reasons surface in lane plan JSON."""
    entries = (
        PlanEntry("r1", 10, "develop", "cursor", "wait", "precondition", "dependencies not satisfied"),
        PlanEntry("r2", 11, "merge", "cursor", "wait", "engine_paused", "cursor paused"),
        PlanEntry("r3", 12, "draft", "claude", "wait", "slot_full", "no slots"),
        PlanEntry("r4", 0, "", None, "wait", "halt", "halted for maintenance"),
    )
    queue_plan = QueuePlan(revision=1, entries=entries, halt=True)
    lane_plan = _lane_plan(tmp_path, queue_plan=queue_plan)
    as_dict = plan_to_dict(lane_plan)
    assert as_dict["halt"] is True
    reasons = {w["issue"]: w["reason"] for w in as_dict["waits"]}
    assert reasons[10] == "precondition"
    assert reasons[11] == "engine_paused"
    assert reasons[12] == "slot_full"
    kinds = {e["kind"] for e in lane_plan.escalations}
    assert "queue_halt" in kinds
    assert "engine_paused" in kinds
    paused = [e for e in lane_plan.escalations if e["kind"] == "engine_paused"]
    assert len(paused) == 1
    assert paused[0]["engine"] == "cursor"


def test_report_suppresses_periodic_report_while_halted_or_paused(tmp_path: Path):
    """Issue #7326: periodic report is quiet while halt or engine_paused waits."""
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={}, report_every_minutes=60)
    past = _T0 - timedelta(hours=2)
    state = {"last_report_at": past.isoformat(), "enqueue": [], "escalations": []}
    base_plan = _lane_plan(tmp_path, ledger=ledger, config=cfg)
    auto = MagicMock(answers=[], escalations=[])

    halted = Ledger(lanes={}, report_every_minutes=60)
    halted_plan = plan(
        halted,
        _snapshot(),
        {},
        cfg,
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
        queue_plan=QueuePlan(
            revision=0,
            entries=(PlanEntry("h", 0, "", None, "wait", "halt", "stopped"),),
            halt=True,
        ),
    )
    state_h = {
        **state,
        "escalations": list(halted_plan.escalations),
    }
    payload_h, _ = report(halted_plan, auto, None, state_h, _T0, ledger)
    assert payload_h["report_due"] is False
    assert payload_h["quiet"] is True

    paused_entries = (
        PlanEntry("p", 5, "develop", "cursor", "wait", "engine_paused", "paused"),
    )
    paused_plan = plan(
        ledger,
        _snapshot(),
        {},
        cfg,
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
        queue_plan=QueuePlan(revision=0, entries=paused_entries, halt=False),
    )
    state_p = {
        **state,
        "escalations": list(paused_plan.escalations),
    }
    payload_p, _ = report(paused_plan, auto, None, state_p, _T0, ledger)
    assert payload_p["report_due"] is False
    assert payload_p["quiet"] is True

    state_ok = {**state, "escalations": list(base_plan.escalations)}
    payload_ok, _ = report(base_plan, auto, None, state_ok, _T0, ledger)
    assert payload_ok["report_due"] is True
    assert payload_ok["quiet"] is False


