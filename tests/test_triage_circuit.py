"""AC-4: triage circuit breaker (#3159)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore

_FIXED_NOW = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
_NOW = _FIXED_NOW.isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "src/**"\n'
    "```\n"
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _write_entry(
    path: Path,
    *,
    reason: str,
    ts: datetime,
) -> None:
    entry = {
        "timestamp": ts.isoformat(),
        "fallback_reason": reason,
        "adopted_order": None,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")






def test_dispatch_skips_llm_when_circuit_open(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "supported_repos": ["sumipan/nexus"],
                "concurrency": {"default": 1},
                "triage": {
                    "circuit_breaker_threshold": 3,
                    "circuit_breaker_reset_seconds": 1800,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(tmp_path))
    reset_config_cache()

    store = _store(tmp_path)
    r_low = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="low",
        requested_by=["a"],
        requested_at=_NOW,
    )
    r_high = store.enqueue(
        issue=2,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="high",
        requested_by=["a"],
        requested_at=_NOW,
    )
    now = _FIXED_NOW
    log = tmp_path / "issuesmith-triage.jsonl"
    for i in range(3):
        _write_entry(
            log,
            reason="llm failed: Command timed out after 60 seconds",
            ts=now - timedelta(minutes=3 - i),
        )

    called = {"n": 0}

    def boom(prompt, **kwargs):
        called["n"] += 1
        raise AssertionError("LLM must not be called when circuit is open")

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "labels": [],
                "title": "t",
                "body": _VALID_BODY,
            }

        def issue_update(self, number, labels_add=None, labels_remove=None):
            pass

        def issue_comment(self, number, body):
            pass

        def get_issue_comments(self, number):
            return []

        def list_issues(self, label, state="open"):
            return []

        def list_open_issues_for_queue(self):
            return []

        def api_request(self, path, **kwargs):
            return []

    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: ["claude"])
    qmod.dispatch_one(
        now=datetime(2026, 9, 11, 6, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        client=Client(),
        store=store,
        skip_seed=True,
        call_llm=boom,
    )
    assert called["n"] == 0
    snap = store.snapshot()
    assert snap.active_order[0] == r_high.request_id
    assert snap.active_order[1] == r_low.request_id

    entries = [
        json.loads(ln)
        for ln in log.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    skipped = [e for e in entries if e.get("fallback_reason") == "circuit_open"]
    assert skipped
    assert skipped[-1].get("circuit_skipped") is True


