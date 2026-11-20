"""Queue stop: drop in_flight and pending requests when an andon stops the line."""

from __future__ import annotations

from issuesmith.queue_store import QueueStore


def _store(tmp_path):
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_drop_stopped_issue_removes_in_flight_and_queues(tmp_path):
    """refs #7464: stopped issues leave in_flight and active_order."""
    store = _store(tmp_path)
    store.add_in_flight(10, "claude", role="implementation", phase="develop")
    store.enqueue(
        issue=10,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
    )
    store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
    )
    rid = store.snapshot().active_order[0]

    assert store.drop_stopped_issue(10, "wf:10:p1:0") is True
    snap = store.snapshot()
    assert snap.in_flight == []
    assert not any(
        store.effective_request(snap, r) and store.effective_request(snap, r).issue == 10
        for r in snap.active_order
    )
    assert snap.stopped == [{"issue": 10, "andon": "wf:10:p1:0"}]
    meta = snap.request_meta[rid]
    assert meta["outcome"] == "stopped"
    assert meta["stopped_by"] == "wf:10:p1:0"


def test_legacy_held_row_cleaned_on_tick(tmp_path, monkeypatch):
    """refs #7464: queue tick drops legacy held rows when andon label is present."""
    import json

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(50, "claude")
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    state["in_flight"][0]["held"] = {"by": "wf:50:p1:0", "at": "2026-01-01T00:00:00+09:00"}
    store.state_path.write_text(json.dumps(state), encoding="utf-8")

    class _Client:
        def issue_get(self, issue, fields=None):
            return {
                "number": issue,
                "state": "OPEN",
                "labels": [{"name": "issuesmith:andon-blocked"}],
            }

        def list_issues(self, **kwargs):
            return []

        def get_issue_comments(self, issue):
            return []

    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    monkeypatch.setattr(qmod, "get_forge", lambda **k: _Client())
    monkeypatch.setattr(qmod, "_github_api_brake_reason", lambda now: None)
    monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
    monkeypatch.setattr(qmod, "_list_open_issues", lambda client: [])
    monkeypatch.setattr(qmod, "deterministic_decision", lambda *a, **k: None)
    monkeypatch.setattr(qmod, "plan", lambda *a, **k: qmod.QueuePlan(revision=0, entries=()))

    qmod.dispatch_one(store=store, skip_seed=True, client=_Client())

    snap = store.snapshot()
    assert snap.in_flight == []
    assert snap.stopped and snap.stopped[0]["issue"] == 50
