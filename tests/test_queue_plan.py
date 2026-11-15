"""Tests for queue plan(), apply(), and status --json (#5689)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from issuesmith.queue import (
    QueuePlan,
    apply,
    plan,
)
from issuesmith.queue_store import QueueStore
from tests.test_queue import _VALID_BODY, _ProdShapeClient

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _ready_client(issue: int) -> _ProdShapeClient:
    return _ProdShapeClient(
        issues={
            issue: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }
        },
        open_issue_rows=[
            {
                "number": issue,
                "title": "t",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
            }
        ],
    )


def _patch_dispatch_gates(monkeypatch, qmod):
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])


def test_plan_api_brake_returns_all_entries_without_forge_reads(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    for issue in (10, 20):
        store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )

    class NoReadClient:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected Forge read: {name}")

    monkeypatch.setattr(
        qmod,
        "_github_api_brake_reason",
        lambda now: "github_api_low (remaining=10, reset=13:00)",
    )
    queue_plan = plan(
        store,
        NoReadClient(),
        now=datetime(2026, 9, 3, 12, 0, tzinfo=_JST),
    )

    assert [entry.issue for entry in queue_plan.entries] == [10, 20]
    assert all(entry.decision == "wait" for entry in queue_plan.entries)
    assert all(entry.reason == "api_brake" for entry in queue_plan.entries)


def test_apply_stale_plan_has_no_side_effects(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _ready_client(10)
    _patch_dispatch_gates(monkeypatch, qmod)
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    queue_plan = plan(store, client, now=now)
    store.enqueue(
        issue=11,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["bob"],
        requested_at=_NOW,
    )
    started = apply(store, client, queue_plan)
    assert started == []
    assert not client.updates
    assert len(store.snapshot().active_order) == 2


def test_global_pipeline_not_idle_on_all_entries(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _ready_client(10)
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: False)
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    queue_plan = plan(store, client, now=now)
    assert len(queue_plan.entries) == 1
    assert queue_plan.entries[0].reason == "pipeline_not_idle"


def test_empty_plan_apply(tmp_path):
    store = _store(tmp_path)
    client = _ready_client(10)
    started = apply(store, client, QueuePlan(revision=0, entries=(), halt=False))
    assert started == []
