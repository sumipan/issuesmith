"""Tests for sub phase enqueue, triage rejection, and dispatch."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from issuesmith.queue_store import QueueStore, QueueValidationError
from issuesmith.queue_triage import DONE_LABEL, READY_LABEL, deterministic_decision

_NOW = datetime.now(timezone.utc).isoformat()


def _store(tmp_path):
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


_VALID_BODY = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```
"""


class TestSubPhaseValidation:
    def test_sub_is_valid_phase(self, tmp_path):
        store = _store(tmp_path)
        result = store.enqueue(
            issue=100,
            phase="sub",
            source="test",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        assert result.created is True

    def test_invalid_phase_rejected(self):
        with pytest.raises(QueueValidationError, match="unknown phase"):
            from issuesmith.queue_store import validate_request_fields

            validate_request_fields(
                issue=1,
                phase="bogus",
                source="s",
                actor_kind="human",
                priority="normal",
                requested_at=_NOW,
                requested_by=["a"],
            )


class TestSubTriage:
    def test_sub_without_milestone_label_rejected(self):
        from issuesmith.queue_store import QueueRequest

        req = QueueRequest(
            request_id="00000000-0000-4000-8000-000000000001",
            issue=100,
            phase="sub",
            source="test",
            actor_kind="human",
            priority="normal",
            requested_at=_NOW,
            requested_by=("alice",),
        )
        issue = {
            "state": "OPEN",
            "body": _VALID_BODY,
            "labels": [{"name": DONE_LABEL["draft"]}],
        }
        decision = deterministic_decision(req, issue)
        assert decision.kind == "rejected"
        assert "scope:milestone" in decision.reason

    def test_sub_with_milestone_label_kept(self):
        from issuesmith.queue_store import QueueRequest

        req = QueueRequest(
            request_id="00000000-0000-4000-8000-000000000002",
            issue=100,
            phase="sub",
            source="test",
            actor_kind="human",
            priority="normal",
            requested_at=_NOW,
            requested_by=("alice",),
        )
        issue = {
            "state": "OPEN",
            "body": _VALID_BODY,
            "labels": [
                {"name": "scope:milestone"},
                {"name": DONE_LABEL["draft"]},
            ],
        }
        decision = deterministic_decision(req, issue)
        assert decision.kind == "keep"


class TestSubDispatch:
    def test_dispatch_sub_adds_sub_ready(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=100,
            phase="sub",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )

        class Client:
            def __init__(self):
                self.updates = []

            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "milestone",
                    "body": _VALID_BODY,
                    "labels": [
                        {"name": "scope:milestone"},
                        {"name": DONE_LABEL["draft"]},
                    ],
                }

            def issue_update(self, number, labels_add=None, labels_remove=None):
                self.updates.append((number, labels_add))

            def issue_comment(self, number, body):
                pass

            def get_issue_comments(self, number):
                return []

            def api_request(self, *args, **kwargs):
                return []

        client = Client()
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == READY_LABEL["sub"]
        assert client.updates == [(100, [READY_LABEL["sub"]])]
