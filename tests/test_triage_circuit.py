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
    fallback_code: str | None = None,
) -> None:
    entry: dict[str, object] = {
        "timestamp": ts.isoformat(),
        "fallback_reason": reason,
        "adopted_order": None,
    }
    if fallback_code is not None:
        entry["fallback_code"] = fallback_code
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


def _triage_snapshot(tmp_path: Path):
    store = _store(tmp_path)
    r = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="normal",
        requested_by=["a"],
        requested_at=_NOW,
    )
    return store, store.snapshot(), r


def test_timeout_records_code_and_circuit_ignores_reason_text(tmp_path) -> None:
    """refs #8141: TimeoutError maps to llm_timeout and circuit ignores reason edits."""
    from issuesmith.queue_triage import is_circuit_open, triage

    store, snap, _r = _triage_snapshot(tmp_path)
    log = tmp_path / "triage.jsonl"

    def timeout_llm(prompt, **kwargs):
        raise TimeoutError("synthetic deadline")

    triage(
        snap,
        issues={1: {"title": "t", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
        store=store,
        call_llm=timeout_llm,
        triage_log_path=log,
        now=_FIXED_NOW,
    )
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row.get("fallback_code") == "llm_timeout"
    assert (row.get("fallback_reason") or "").startswith("llm failed:")

    for i in range(2):
        _write_entry(
            log,
            reason="llm failed: Command timed out after 60 seconds",
            ts=_FIXED_NOW - timedelta(minutes=2 - i),
            fallback_code="llm_timeout",
        )
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=_FIXED_NOW) is True

    lines = log.read_text(encoding="utf-8").splitlines()
    mutated = json.loads(lines[-1])
    mutated["fallback_reason"] = "llm failed: totally different wording"
    lines[-1] = json.dumps(mutated)
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=_FIXED_NOW) is True


def test_non_timeout_failure_records_llm_failed(tmp_path) -> None:
    """refs #8141: parse failures record llm_failed and do not open the circuit."""
    from issuesmith.queue_triage import is_circuit_open, triage

    store, snap, _r = _triage_snapshot(tmp_path / "run")
    log = tmp_path / "run" / "triage.jsonl"

    def bad_json_llm(prompt, **kwargs):
        class R:
            text = "not-json"
            body = "not-json"
            usage = None

        return R()

    triage(
        snap,
        issues={1: {"title": "t", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
        store=store,
        call_llm=bad_json_llm,
        triage_log_path=log,
        now=_FIXED_NOW,
    )
    row = json.loads(log.read_text(encoding="utf-8").strip())
    assert row.get("fallback_code") == "llm_failed"

    for i in range(2):
        _write_entry(
            log,
            reason="llm failed: parse error",
            ts=_FIXED_NOW - timedelta(minutes=2 - i),
            fallback_code="llm_failed",
        )
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=_FIXED_NOW) is False


def test_legacy_reason_only_entries_still_open_circuit(tmp_path) -> None:
    """refs #8141: legacy log rows without fallback_code still trip the breaker."""
    from issuesmith.queue_triage import is_circuit_open

    log = tmp_path / "legacy.jsonl"
    now = _FIXED_NOW
    for i in range(3):
        _write_entry(
            log,
            reason="llm failed: Command timed out after 60 seconds",
            ts=now - timedelta(minutes=3 - i),
        )
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=now) is True


def test_code_takes_precedence_over_timeout_words(tmp_path) -> None:
    """refs #8141: llm_failed code blocks breaker even when reason mentions timeout."""
    from issuesmith.queue_triage import is_circuit_open

    log = tmp_path / "coded.jsonl"
    now = _FIXED_NOW
    for i in range(3):
        _write_entry(
            log,
            reason="llm failed: Command timed out after 60 seconds",
            ts=now - timedelta(minutes=3 - i),
            fallback_code="llm_failed",
        )
    assert is_circuit_open(log, threshold=3, reset_seconds=1800, now=now) is False


