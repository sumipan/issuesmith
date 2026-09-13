"""AC-12: triage must not reject for deps-waiting reasons (#3130)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issuesmith.queue_store import QueueStore
from issuesmith.queue_triage import is_deps_waiting_reject_reason, triage

_NOW = datetime.now(timezone.utc).isoformat()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_is_deps_waiting_reject_reason_matches():
    assert is_deps_waiting_reject_reason("deps_blocked: waiting for #3162") is True
    assert is_deps_waiting_reject_reason("依存未解決のためスキップ") is True
    assert is_deps_waiting_reject_reason("waiting for deps #3162") is True
    assert is_deps_waiting_reject_reason("duplicate of #1") is False
    assert is_deps_waiting_reject_reason("obsolete bump") is False


def test_triage_downgrades_deps_reject_to_keep(tmp_path: Path):
    store = _store(tmp_path)
    rid = store.enqueue(
        issue=3166,
        phase="develop",
        source="milestone-chain",
        actor_kind="automation",
        priority="normal",
        requested_by=["milestone-chain"],
        requested_at=_NOW,
    ).request_id
    snap = store.snapshot()

    def fake_llm(prompt: str, **kwargs):
        payload = {
            "order": [rid],
            "decisions": [
                {
                    "request_id": rid,
                    "decision": "reject",
                    "reason": "deps_not_resolved waiting for #3162",
                    "uncertain_flag": False,
                }
            ],
        }
        return type("R", (), {"text": json.dumps(payload), "usage": None})()

    result = triage(
        snap,
        issues={
            3166: {
                "number": 3166,
                "title": "child",
                "body": "```yaml\ntarget_repo: sumipan/nexus\nbase_branch: main\nallow_paths:\n  - src/**\n```\n",
                "labels": [{"name": "issuesmith:draft-done"}],
                "state": "OPEN",
            }
        },
        engine="cursor",
        model="auto",
        call_llm=fake_llm,
        store=store,
        triage_log_path=tmp_path / "triage.jsonl",
    )
    assert result.adopted is True
    assert len(result.decisions) == 1
    assert result.decisions[0].decision == "keep"
    assert "deps reject ignored" in result.decisions[0].reason
