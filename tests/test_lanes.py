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
    classify,
    ledger_state_path,
    load_ledger,
    plan,
    report,
    resolve_report_since,
)
from issuesmith.queue_store import QueueRequest, QueueSnapshot


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
        last_issue=None,
        halt=False,
        halt_reason=None,
        requests=requests,
        in_flight=list(in_flight or []),
    )


def test_classify_blocked_and_candidate(tmp_path: Path):
    cfg = _cfg(tmp_path)
    status, phase = classify(("issuesmith:andon-decision",), cfg)
    assert status == "blocked"
    assert phase is None
    status2, phase2 = classify((), cfg)
    assert status2 == "candidate"
    assert phase2 == "draft"


def test_plan_one_enqueue_from_first_lane(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"a": [1], "b": [2]})
    issues = {
        1: IssueView(1, ()),
        2: IssueView(2, ()),
    }
    now = datetime.now(timezone.utc)
    snap = _snapshot()
    role_map = {"design": "claude", "implementation": "cursor"}
    lane_plan = plan(ledger, snap, issues, cfg, now, False, role_engine_map=role_map)
    assert len(lane_plan.enqueue) == 1
    assert lane_plan.enqueue[0]["issue"] == 1


def test_hold_skipped_lane_blocked(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"lane": [10, 11]}, hold={10})
    issues = {
        11: IssueView(11, ("issuesmith:andon-decision",)),
    }
    now = datetime.now(timezone.utc)
    snap = _snapshot()
    lane_plan = plan(
        ledger,
        snap,
        issues,
        cfg,
        now,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    assert lane_plan.candidates == []
    assert any(e["kind"] == "lane_blocked" for e in lane_plan.escalations)


def test_stale_queued_and_api_low(tmp_path: Path):
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={"a": [1]})
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    snap = _snapshot(active=[(5, "develop", old)])
    issues = {1: IssueView(1, ())}
    now = datetime.now(timezone.utc)
    lane_plan = plan(
        ledger,
        snap,
        issues,
        cfg,
        now,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    assert any(e["kind"] == "stale_queued" for e in lane_plan.escalations)

    api_plan = plan(
        ledger,
        snap,
        issues,
        cfg,
        now,
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
        datetime.now(timezone.utc),
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
        datetime.now(timezone.utc),
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    auto = MagicMock(escalations=[])
    payload, _ = report(lane_plan, auto, None, {}, datetime.now(timezone.utc), ledger)
    assert payload["report_due"] is True

    past = datetime.now(timezone.utc) - timedelta(hours=3)
    state = {"last_report_at": past.isoformat(), "enqueue": [], "escalations": []}
    payload2, _ = report(lane_plan, auto, None, state, datetime.now(timezone.utc), ledger)
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
