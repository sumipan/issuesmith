"""Tests for sub phase enqueue, triage rejection, and dispatch."""

from __future__ import annotations

from datetime import datetime, timezone

from issuesmith.queue_store import QueueStore
from issuesmith.queue_triage import DONE_LABEL, deterministic_decision

_FIXED_NOW = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
_NOW = _FIXED_NOW.isoformat()


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


