"""Priority barrier: low waits while any high request is active (#6254)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from issuesmith.queue import plan
from issuesmith.queue_store import QueueStore
from tests.test_queue import _VALID_BODY, _ProdShapeClient

_JST = ZoneInfo("Asia/Tokyo")
_PLAN_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=_JST)
_NOW = _PLAN_NOW.astimezone(timezone.utc).isoformat()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _client_for_issues(*issue_numbers: int) -> _ProdShapeClient:
    issues = {
        n: {
            "state": "OPEN",
            "title": "t",
            "body": _VALID_BODY,
            "labels": [],
        }
        for n in issue_numbers
    }
    rows = [
        {
            "number": n,
            "title": "t",
            "state": "open",
            "body": _VALID_BODY,
            "labels": [],
        }
        for n in issue_numbers
    ]
    return _ProdShapeClient(issues=issues, open_issue_rows=rows)


def _patch_gates(monkeypatch, qmod):
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])


def _enqueue_high_low_after_deps(store: QueueStore) -> tuple[str, str]:
    high = store.enqueue(
        issue=100,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="high",
        requested_by=["alice"],
        requested_at=_NOW,
        after=[99],
    )
    low = store.enqueue(
        issue=200,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="low",
        requested_by=["bob"],
        requested_at=_NOW,
    )
    return high.request_id, low.request_id


def test_plan_low_waits_with_priority_barrier_while_high_waits(tmp_path, monkeypatch):
    """AC1: low waits with priority_barrier while high is blocked on after_deps."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    _, low_rid = _enqueue_high_low_after_deps(store)
    client = _client_for_issues(99, 100, 200)
    _patch_gates(monkeypatch, qmod)

    queue_plan = plan(store, client, now=_PLAN_NOW)
    by_id = {e.request_id: e for e in queue_plan.entries}
    low_entry = by_id[low_rid]
    assert low_entry.decision == "wait"
    assert low_entry.reason == "priority_barrier"
    assert low_entry.detail == "waiting high: #100"
