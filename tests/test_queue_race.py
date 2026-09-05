"""#2813: queue dispatch race — ready-label gate, dispatch_lock, audit in-flight."""
from __future__ import annotations

import argparse
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_dispatch_blocks_when_ready_label_exists(tmp_path, monkeypatch):
    """別 Issue が -ready のとき dispatch_one は another issue is running で拒否する."""
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }

        def list_issues(self, label, state="open"):
            if label == "issuesmith:draft-ready":
                return [{"number": 99, "labels": [{"name": label}]}]
            return []

        def get_issue_comments(self, number):
            return []

        def list_open_issues_for_queue(self):
            return []

        def issue_update(self, number, labels_add=None, labels_remove=None):
            pass

        def issue_comment(self, number, body):
            pass

    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(
        now=now,
        client=Client(),
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 50


def test_concurrent_dispatch_single_winner(tmp_path, monkeypatch):
    """2 スレッド同時 dispatch_one では成功が最大 1 回（dispatch_lock + in-flight）."""
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    assert store.dispatch_lock_path != store.lock_path
    assert store.dispatch_lock_path.name == "dispatch.lock"

    # Nested dispatch_lock + store.lock must not deadlock.
    with store.dispatch_lock():
        store.snapshot()
        store.set_last_issue(None)

    for issue in (50, 51):
        store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )

    class Client:
        def __init__(self):
            self._lock = threading.Lock()
            self._labels: dict[int, set[str]] = {50: set(), 51: set()}
            self.updates: list[tuple] = []

        def issue_get(self, number, fields=None):
            with self._lock:
                labels = [{"name": n} for n in self._labels.get(number, set())]
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": labels,
            }

        def issue_update(self, number, labels_add=None, labels_remove=None):
            with self._lock:
                self.updates.append((number, labels_add))
                bucket = self._labels.setdefault(number, set())
                for lab in labels_add or []:
                    bucket.add(lab)
                for lab in labels_remove or []:
                    bucket.discard(lab)

        def issue_comment(self, number, body):
            return None

        def get_issue_comments(self, number):
            return []

        def list_issues(self, label, state="open"):
            with self._lock:
                return [
                    {"number": num, "labels": [{"name": label}]}
                    for num, labs in self._labels.items()
                    if label in labs
                ]

        def list_open_issues_for_queue(self):
            return []

    client = Client()
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    barrier = threading.Barrier(2)
    results: list = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        out = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        with results_lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    wins = [r for r in results if r.dispatched]
    assert len(wins) <= 1
    assert len(wins) == 1
    assert wins[0].reason == "dispatched"


def test_audit_detects_multiple_in_flight(tmp_path, capsys):
    """2 件以上が同時に -ready/-running なら AUDIT FAIL + exit 1."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)

    class FakeClient:
        def list_issues(self, label, state="open"):
            if label == "issuesmith:draft-running":
                return [{"number": 2805}, {"number": 2808}]
            return []

    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        offline=False,
    )
    with patch.object(qmod, "GitHubClient", return_value=FakeClient()):
        code = qmod._cmd_audit(args)
    captured = capsys.readouterr()
    assert code == 1
    assert "AUDIT FAIL: multiple issues in-flight:" in captured.out
    assert "#2805" in captured.out
    assert "#2808" in captured.out


def test_audit_offline_skips_github(tmp_path, capsys):
    """--offline では GitHub API を呼ばずローカル検査のみ."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        offline=True,
    )

    def boom(*a, **k):
        raise AssertionError("GitHubClient must not be constructed in offline mode")

    with patch.object(qmod, "GitHubClient", side_effect=boom):
        code = qmod._cmd_audit(args)
    assert code == 0
    assert "AUDIT OK" in capsys.readouterr().out
