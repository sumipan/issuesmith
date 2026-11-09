"""Priority barrier: low waits while any high request is active (#6254)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from issuesmith.config import ConcurrencyConfig, get_config
from issuesmith.queue import dispatch_one, plan
from issuesmith.queue_store import QueueStore
from tests.test_queue import _VALID_BODY, _ProdShapeClient

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")
_PLAN_NOW = datetime(2026, 11, 9, 12, 0, tzinfo=_JST)


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


def test_dispatch_one_does_not_dispatch_low_while_high_active(tmp_path, monkeypatch):
    """AC2: dispatch_one does not label low while high is waiting."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    _enqueue_high_low_after_deps(store)
    client = _client_for_issues(99, 100, 200)
    _patch_gates(monkeypatch, qmod)

    result = dispatch_one(
        now=_PLAN_NOW,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is False
    low_updates = [u for u in client.updates if u[0] == 200]
    assert not any(
        "issuesmith:draft-ready" in (labels_add or [])
        for _, labels_add, _ in low_updates
    )
    snap = store.snapshot()
    assert any(
        store.effective_request(snap, rid) is not None
        and store.effective_request(snap, rid).issue == 200
        for rid in snap.active_order
    )


def test_plan_low_dispatches_when_no_high_active(tmp_path, monkeypatch):
    """AC3: dispatchable low proceeds when no high is active."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    low = store.enqueue(
        issue=200,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="low",
        requested_by=["bob"],
        requested_at=_NOW,
    )
    client = _client_for_issues(200)
    _patch_gates(monkeypatch, qmod)

    queue_plan = plan(store, client, now=_PLAN_NOW)
    entry = next(e for e in queue_plan.entries if e.request_id == low.request_id)
    assert entry.decision == "dispatch"
    assert entry.reason == "ok"


def test_plan_normal_dispatches_while_high_waits(tmp_path, monkeypatch):
    """AC4: normal priority still dispatches while high waits."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.enqueue(
        issue=100,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="high",
        requested_by=["alice"],
        requested_at=_NOW,
        after=[99],
    )
    normal = store.enqueue(
        issue=150,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["carol"],
        requested_at=_NOW,
    )
    client = _client_for_issues(99, 100, 150)
    _patch_gates(monkeypatch, qmod)

    queue_plan = plan(store, client, now=_PLAN_NOW)
    by_id = {e.request_id: e for e in queue_plan.entries}
    normal_entry = by_id[normal.request_id]
    assert normal_entry.decision == "dispatch"
    assert normal_entry.reason == "ok"


@pytest.mark.parametrize("strict_order", [False, True])
def test_plan_priority_barrier_independent_of_strict_order(
    tmp_path, monkeypatch, strict_order
):
    """AC5: low reason is priority_barrier regardless of concurrency.strict_order."""
    from issuesmith import queue as qmod

    cfg = get_config()
    monkeypatch.setattr(
        qmod,
        "get_config",
        lambda: replace(
            cfg,
            concurrency=ConcurrencyConfig(
                default=1,
                per_engine={"claude": 1, "cursor": 1},
                strict_order=strict_order,
                max_dispatch_per_tick=cfg.concurrency.max_dispatch_per_tick,
            ),
        ),
    )
    store = _store(tmp_path)
    store.add_in_flight(
        1,
        "claude",
        role="design",
        allow_paths=("unrelated/**",),
        target_repo="sumipan/nexus",
    )
    store.enqueue(
        issue=100,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="high",
        requested_by=["alice"],
        requested_at=_NOW,
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
    client = _client_for_issues(100, 200)
    _patch_gates(monkeypatch, qmod)

    queue_plan = plan(store, client, now=_PLAN_NOW)
    low_entry = next(e for e in queue_plan.entries if e.request_id == low.request_id)
    assert low_entry.decision == "wait"
    assert low_entry.reason == "priority_barrier"
    assert low_entry.detail == "waiting high: #100"
