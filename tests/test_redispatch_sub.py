"""tests/test_redispatch_sub.py — redispatch --phase sub。"""
from __future__ import annotations

from unittest.mock import MagicMock

from issuesmith import recovery
from issuesmith.queue import redispatch_label_plan
from issuesmith.queue_triage import DONE_LABEL, READY_LABEL, RUNNING_LABEL


def test_redispatch_label_plan_sub():
    required, to_remove = redispatch_label_plan("sub")
    assert required == frozenset({DONE_LABEL["draft"], "scope:milestone"})
    assert to_remove == frozenset(
        {
            READY_LABEL["sub"],
            RUNNING_LABEL["sub"],
            DONE_LABEL["sub"],
        }
    )


def test_build_parser_accepts_sub_phase():
    parser = recovery.build_parser()
    args = parser.parse_args(["redispatch", "42", "--phase", "sub"])
    assert args.phase == "sub"


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

    rc = recovery.cmd_redispatch(42, phase="sub", reason="restore")
    assert rc == 0
    store.remove_in_flight.assert_called_once_with(42)
    store.enqueue.assert_called_once()
    assert store.enqueue.call_args.kwargs["phase"] == "sub"


def test_cmd_redispatch_draft_also_clears_in_flight(monkeypatch):
    store = MagicMock()
    client = MagicMock()
    client.issue_get.return_value = {"labels": []}
    plan = recovery.Plan(
        action="redispatch",
        reason="test",
        command="x",
        required_labels=frozenset(),
        blocked_by=None,
    )
    monkeypatch.setattr(recovery, "plan", lambda *_a, **_k: plan)
    monkeypatch.setattr(recovery, "_github_client", lambda: client)
    monkeypatch.setattr(recovery, "QueueStore", lambda: store)
    monkeypatch.setattr(recovery, "apply_redispatch_labels", lambda *_a, **_k: False)
    store.enqueue.return_value = MagicMock(request_id="rid", created=True)

    assert recovery.cmd_redispatch(9, phase="draft") == 0
    store.remove_in_flight.assert_called_once_with(9)
