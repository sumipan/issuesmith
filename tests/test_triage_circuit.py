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

_NOW = datetime.now(timezone.utc).isoformat()
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


def test_is_circuit_open_true_on_consecutive_timeouts(tmp_path):
    from issuesmith.queue_triage import is_circuit_open

    log = tmp_path / "triage.jsonl"
    now = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
    for i in range(3):
        _write_entry(
            log,
            reason="llm failed: timed out after 60 seconds",
            ts=now - timedelta(minutes=5 - i),
        )
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=now) is True


def test_is_circuit_open_false_when_mixed_or_stale(tmp_path):
    from issuesmith.queue_triage import is_circuit_open

    log = tmp_path / "triage.jsonl"
    now = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
    _write_entry(
        log,
        reason="llm failed: timed out after 60 seconds",
        ts=now - timedelta(minutes=2),
    )
    _write_entry(
        log,
        reason="llm failed: boom",
        ts=now - timedelta(minutes=1),
    )
    _write_entry(
        log,
        reason="llm failed: timed out after 60 seconds",
        ts=now,
    )
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=now) is False

    log2 = tmp_path / "stale.jsonl"
    old = now - timedelta(seconds=1801)
    for i in range(3):
        _write_entry(
            log2,
            reason="llm failed: timed out after 60 seconds",
            ts=old + timedelta(seconds=i),
        )
    assert is_circuit_open(log2, threshold=3, reset_seconds=1800, now=now) is False


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
    now = datetime(2026, 9, 11, 6, 0, tzinfo=timezone.utc)
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


def test_triage_config_defaults_and_override(tmp_path, monkeypatch):
    from issuesmith.config import TriageConfig, load_config

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "sumipan/nexus"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config()
    assert cfg.triage == TriageConfig()
    assert cfg.triage.enabled is True
    assert cfg.triage.engine == "claude"
    assert cfg.triage.model == "claude-sonnet-4-6"
    assert cfg.triage.timeout == 60
    assert cfg.triage.body_chars == 500
    assert cfg.triage.circuit_breaker_threshold == 3
    assert cfg.triage.circuit_breaker_reset_seconds == 1800

    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "triage": {
                    "enabled": False,
                    "timeout": 120,
                    "body_chars": 0,
                    "model": "claude-opus-4-6",
                    "circuit_breaker_threshold": 5,
                },
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg2 = load_config()
    assert cfg2.triage.enabled is False
    assert cfg2.triage.timeout == 120
    assert cfg2.triage.body_chars == 0
    assert cfg2.triage.model == "claude-opus-4-6"
    assert cfg2.triage.circuit_breaker_threshold == 5
    assert cfg2.triage.engine == "claude"
