"""Tests for queue plan(), apply(), and status --json (#5689)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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


@pytest.mark.parametrize(
    "reason",
    [
        "window",
        "halt",
        "engine_paused",
        "slot_full",
        "issue_fetch_failed",
        "decision_pending",
        "precondition",
        "after_deps",
        "deletion_refs",
        "allow_paths_conflict",
        "last_issue_not_terminal",
        "last_issue_fetch_failed",
        "pipeline_not_idle",
        "max_dispatch_reached",
    ],
)
def test_plan_reason_is_declared_member(reason):
    assert reason in {
        "ok",
        "api_brake",
        "window",
        "halt",
        "engine_paused",
        "slot_full",
        "issue_fetch_failed",
        "decision_pending",
        "precondition",
        "after_deps",
        "deletion_refs",
        "allow_paths_conflict",
        "last_issue_not_terminal",
        "last_issue_fetch_failed",
        "pipeline_not_idle",
        "max_dispatch_reached",
    }


def test_plan_is_read_only(tmp_path, monkeypatch):
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
    rev_before = store.snapshot().revision
    updates_before = len(client.updates)
    comments_before = len(client.comments)

    queue_plan = plan(store, client, now=now)

    assert store.snapshot().revision == rev_before
    assert len(client.updates) == updates_before
    assert len(client.comments) == comments_before
    assert len(queue_plan.entries) == 1


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


def test_dispatch_uses_custom_seed_window(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.enqueue(
        issue=10,
        phase="draft",
        source="release-watcher",
        actor_kind="automation",
        priority="normal",
        requested_by=["release-watcher"],
        requested_at=_NOW,
    )
    custom_seed = tmp_path / "custom-seed.yml"
    monkeypatch.setattr(
        qmod,
        "seed_window",
        lambda path: ("00:00", "23:59", 5)
        if path == custom_seed
        else ("01:00", "07:00", 5),
    )
    _patch_dispatch_gates(monkeypatch, qmod)

    result = qmod.dispatch_one(
        now=datetime(2026, 9, 3, 12, 0, tzinfo=_JST),
        client=_ready_client(10),
        store=store,
        seed_path=custom_seed,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )

    assert result.dispatched is True


def test_plan_three_mixed_reasons(tmp_path, monkeypatch):
    from dataclasses import replace

    from issuesmith import queue as qmod
    from issuesmith.config import ConcurrencyConfig, get_config

    cfg = get_config()
    monkeypatch.setattr(
        qmod,
        "get_config",
        lambda: replace(
            cfg,
            concurrency=ConcurrencyConfig(
                default=5,
                per_engine={"claude": 5, "cursor": 5},
                strict_order=cfg.concurrency.strict_order,
                max_dispatch_per_tick=cfg.concurrency.max_dispatch_per_tick,
            ),
        ),
    )

    store = _store(tmp_path)
    r_ok = store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["a"],
        requested_at=_NOW,
    )
    r_pause = store.enqueue(
        issue=20,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["b"],
        requested_at=_NOW,
    )
    r_conflict = store.enqueue(
        issue=30,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["c"],
        requested_at=_NOW,
    )
    store.add_in_flight(
        99,
        "cursor",
        role="implementation",
        phase="develop",
        allow_paths=("tools/**",),
        target_repo="sumipan/nexus",
    )
    client = _ProdShapeClient(
        issues={
            10: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            },
            20: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:plan-done"}],
            },
            30: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:plan-done"}],
            },
        },
        open_issue_rows=[
            {"number": 10, "title": "t", "state": "open", "body": _VALID_BODY, "labels": []},
                {
                    "number": 20,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:plan-done"}],
                },
                {
                    "number": 30,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:plan-done"}],
                },
        ],
    )

    impl_pause_checks = 0

    def paused(role=None, **kwargs):
        nonlocal impl_pause_checks
        if role == "implementation":
            impl_pause_checks += 1
            if impl_pause_checks == 1:
                return ["cursor"]
        return []

    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", paused)
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    queue_plan = plan(store, client, now=now)
    by_id = {e.request_id: e for e in queue_plan.entries}
    ok_entry = by_id[r_ok.request_id]
    assert ok_entry.decision == "dispatch", f"{ok_entry.reason}: {ok_entry.detail}"
    assert ok_entry.reason == "ok"
    assert by_id[r_pause.request_id].decision == "wait"
    assert by_id[r_pause.request_id].reason == "engine_paused"
    assert by_id[r_conflict.request_id].decision == "wait"
    assert by_id[r_conflict.request_id].reason == "allow_paths_conflict"


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


def test_apply_dispatches_only_ok_entries(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r_ok = store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["a"],
        requested_at=_NOW,
    )
    store.enqueue(
        issue=20,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["b"],
        requested_at=_NOW,
    )
    client = _ProdShapeClient(
        issues={
            10: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:develop-done"}],
            },
            20: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            },
        },
        open_issue_rows=[
            {"number": 10, "title": "t", "state": "open", "body": _VALID_BODY, "labels": []},
            {"number": 20, "title": "t", "state": "open", "body": _VALID_BODY, "labels": []},
        ],
    )

    def paused(role=None, **kwargs):
        if role == "implementation":
            return ["cursor"]
        return []

    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", paused)
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    queue_plan = plan(store, client, now=now)
    started = apply(store, client, queue_plan)
    assert len(started) == 1
    assert started[0].request_id == r_ok.request_id
    assert r_ok.request_id not in store.snapshot().active_order
    assert len(store.snapshot().active_order) == 1


def test_plan_max_dispatch_per_tick(tmp_path, monkeypatch):
    from dataclasses import replace

    from issuesmith import queue as qmod
    from issuesmith.config import ConcurrencyConfig, get_config

    cfg = get_config()
    monkeypatch.setattr(
        qmod,
        "get_config",
        lambda: replace(
            cfg,
            concurrency=ConcurrencyConfig(
                default=2,
                per_engine={"claude": 5, "cursor": 5},
                strict_order=cfg.concurrency.strict_order,
                max_dispatch_per_tick=2,
            ),
        ),
    )

    store = _store(tmp_path)
    ids = []
    for issue in (10, 11, 12):
        r = store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["a"],
            requested_at=_NOW,
        )
        ids.append(r.request_id)
    client = _ProdShapeClient(
        issues={
            n: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }
            for n in (10, 11, 12)
        },
        open_issue_rows=[
            {
                "number": n,
                "title": "t",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
            }
            for n in (10, 11, 12)
        ],
    )
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    queue_plan = plan(store, client, now=now)
    dispatch_entries = [e for e in queue_plan.entries if e.decision == "dispatch"]
    wait_max = [e for e in queue_plan.entries if e.reason == "max_dispatch_reached"]
    assert len(dispatch_entries) == 2
    assert len(wait_max) == 1
    assert wait_max[0].request_id == ids[2]


def test_status_json_output(tmp_path, monkeypatch):
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
    monkeypatch.setattr(qmod, "get_forge", lambda **kwargs: client)
    args = argparse.Namespace(
        queue_path=str(tmp_path / "queue.jsonl"),
        state_path=str(tmp_path / "state.json"),
        lock_path=str(tmp_path / "lock"),
        json=True,
    )
    import io
    import sys

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    assert qmod._cmd_status(args) == 0
    payload = json.loads(captured.getvalue())
    assert set(payload) == {
        "revision",
        "active",
        "halt",
        "halt_reason",
        "last_issue",
        "in_flight",
        "plan",
    }
    assert payload["active"] == 1
    assert len(payload["plan"]) == 1
    entry = payload["plan"][0]
    assert set(entry) == {
        "request_id",
        "issue",
        "phase",
        "engine",
        "decision",
        "reason",
        "detail",
    }


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
