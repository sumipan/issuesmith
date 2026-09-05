"""F1: ISSUESMITH_QUEUE_DIR による本番キュー／triage 隔離の回帰テスト."""
from __future__ import annotations

import json
import os
from pathlib import Path

from issuesmith.queue_store import (
    DEFAULT_QUEUE_PATH,
    DEFAULT_TRIAGE_LOG_PATH,
    QueueStore,
)


def _fingerprint(path: Path) -> tuple[bool, str | None, int | None]:
    if not path.exists():
        return False, None, None
    return True, path.read_text(encoding="utf-8"), path.stat().st_mtime_ns


def test_queue_store_defaults_respect_issuesmith_queue_dir(tmp_path, monkeypatch):
    """ISSUESMITH_QUEUE_DIR 設定時、QueueStore() は本番ではなく env 配下へ書く."""
    queue_dir = tmp_path / "isolated-queue"
    queue_dir.mkdir()
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(queue_dir))

    before_queue = _fingerprint(DEFAULT_QUEUE_PATH)
    before_triage = _fingerprint(DEFAULT_TRIAGE_LOG_PATH)

    store = QueueStore()
    result = store.enqueue(
        issue=99901,
        phase="draft",
        source="isolation-test",
        actor_kind="automation",
        priority="low",
        requested_by=["test"],
    )
    assert result.created is True

    env_queue = queue_dir / "issuesmith-queue.jsonl"
    assert env_queue.exists()
    lines = [json.loads(line) for line in env_queue.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(row.get("issue") == 99901 for row in lines)

    assert _fingerprint(DEFAULT_QUEUE_PATH) == before_queue
    assert _fingerprint(DEFAULT_TRIAGE_LOG_PATH) == before_triage


def test_explicit_paths_override_env_dir(tmp_path, monkeypatch):
    """明示 path は ISSUESMITH_QUEUE_DIR より優先される."""
    env_dir = tmp_path / "env-dir"
    env_dir.mkdir()
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(env_dir))

    explicit = tmp_path / "explicit"
    store = QueueStore(
        queue_path=explicit / "q.jsonl",
        state_path=explicit / "s.json",
        lock_path=explicit / "lock",
    )
    store.enqueue(
        issue=1,
        phase="draft",
        source="t",
        actor_kind="human",
        priority="normal",
        requested_by=["a"],
    )
    assert (explicit / "q.jsonl").exists()
    assert not (env_dir / "issuesmith-queue.jsonl").exists()


def test_dispatch_one_triage_log_uses_env_dir(tmp_path, monkeypatch):
    """dispatch_one の triage_log も ISSUESMITH_QUEUE_DIR 配下を使い、本番を汚さない."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    queue_dir = tmp_path / "dispatch-iso"
    queue_dir.mkdir()
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(queue_dir))

    before_triage = _fingerprint(DEFAULT_TRIAGE_LOG_PATH)

    store = QueueStore()
    store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
    )

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": "```yaml\ntarget_repo: sumipan/nexus\n```\n",
                "labels": [],
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

    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    qmod.dispatch_one(
        now=now,
        client=Client(),
        store=store,
        skip_seed=True,
        call_llm=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )

    assert _fingerprint(DEFAULT_TRIAGE_LOG_PATH) == before_triage
    assert os.environ.get("ISSUESMITH_QUEUE_DIR") == str(queue_dir)


def test_production_queue_unchanged_after_default_store_ops(tmp_path, monkeypatch):
    """AC: ISSUESMITH_QUEUE_DIR 下での操作で本番 jsonl が変化しない."""
    queue_dir = tmp_path / "ac-queue"
    queue_dir.mkdir()
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(queue_dir))

    before_q = _fingerprint(DEFAULT_QUEUE_PATH)
    before_t = _fingerprint(DEFAULT_TRIAGE_LOG_PATH)

    store = QueueStore()
    r = store.enqueue(
        issue=12345,
        phase="draft",
        source="release-watcher",
        actor_kind="automation",
        priority="low",
        requested_by=["release-watcher"],
    )
    store.complete(r.request_id, "dequeued", extra_meta={"reason": "isolation"})

    assert _fingerprint(DEFAULT_QUEUE_PATH) == before_q
    assert _fingerprint(DEFAULT_TRIAGE_LOG_PATH) == before_t
