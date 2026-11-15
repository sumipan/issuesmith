"""tests/test_redispatch_sub.py — redispatch --phase subc3002"""
from __future__ import annotations

from unittest.mock import MagicMock

from issuesmith import recovery


def test_cmd_redispatch_sub_removes_in_flight_and_applies_labels(monkeypatch):
    store = MagicMock()
    client = MagicMock()
    client.issue_get.return_value = {
        "labels": [
            {"name": "issuesmith:draft-done"},
            {"name": "scope:milestone"},
            {"name": "issuesmith:sub-running"},
        ]
    }
    plan = recovery.Plan(
        action="redispatch",
        reason="test",
        command="x",
        required_labels=frozenset({"issuesmith:draft-done", "scope:milestone"}),
        blocked_by=None,
    )
    monkeypatch.setattr(recovery, "plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(recovery, "_github_client", lambda: client)
    monkeypatch.setattr(recovery, "QueueStore", lambda: store)
    monkeypatch.setattr(
        recovery,
        "apply_redispatch_labels",
        lambda *_a, **_k: True,
    )
    store.enqueue.return_value = MagicMock(request_id="rid", created=True)

    rc = recovery.requeue_phase(42, phase="sub", reason="restore")
    assert rc == 0
    store.remove_in_flight.assert_called_once_with(42)
    store.enqueue.assert_called_once()
    assert store.enqueue.call_args.kwargs["phase"] == "sub"


