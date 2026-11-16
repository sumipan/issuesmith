"""Regression: queue dispatch no longer gates on legacy last_issue (#5763)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from issuesmith import queue as qmod
from issuesmith.queue_store import QueueStore
from tests.test_queue import _VALID_BODY, _store

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).isoformat()


def _write_legacy_last_issue(tmp_path, issue):
    """Leave a serial-era ``last_issue`` key in state.json (the store no longer writes it)."""
    path = tmp_path / "state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "revision": 0}
    state["last_issue"] = issue
    path.write_text(json.dumps(state), encoding="utf-8")


def _enqueue_draft(store: QueueStore, issue: int) -> None:
    store.enqueue(
        issue=issue,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )


def test_status_omits_last_issue_and_does_not_fetch_it(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path)
    _write_legacy_last_issue(tmp_path, 40)
    _enqueue_draft(store, 50)

    class Client:
        def issue_get(self, number, fields=None):
            if number == 40:
                raise AssertionError("status must not fetch last_issue")
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }

        def list_open_issues_for_queue(self):
            return []

    monkeypatch.setattr(qmod, "get_forge", lambda **kwargs: Client())
    code = qmod.main(
        [
            "--queue-path",
            str(tmp_path / "queue.jsonl"),
            "--state-path",
            str(tmp_path / "state.json"),
            "--lock-path",
            str(tmp_path / "lock"),
            "status",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "last_issue" not in payload
