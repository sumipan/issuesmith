"""F4: issuesmith.queue dequeue サブコマンドのテスト."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.exceptions import GitHubApiError

REPO_ROOT = Path(__file__).resolve().parents[3]
from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_dequeue_removes_from_active_and_comments(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    result = store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    rid = result.request_id

    client = MagicMock()
    client.issue_get.return_value = {"number": 50, "state": "OPEN"}
    monkeypatch.setattr(qmod, "GitHubClient", lambda **kwargs: client)
    monkeypatch.setattr(qmod, "QueueStore", lambda **kwargs: store)

    code = qmod.main(
        [
            "dequeue",
            "--request-id",
            rid,
            "--reason",
            "test cleanup",
        ]
    )
    assert code == 0

    snap = store.snapshot()
    assert rid not in snap.active_order
    assert rid in snap.completed_request_ids
    meta = snap.request_meta.get(rid) or {}
    assert meta.get("outcome") == "dequeued"
    assert meta.get("reason") == "test cleanup"

    client.issue_get.assert_called()
    comment_bodies = [c.args[1] for c in client.issue_comment.call_args_list]
    assert any(rid in body and "dequeued" in body and "test cleanup" in body for body in comment_bodies)


def test_dequeue_skips_comment_on_404(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    result = store.enqueue(
        issue=12345,
        phase="draft",
        source="release-watcher",
        actor_kind="automation",
        priority="low",
        requested_by=["release-watcher"],
        requested_at=_NOW,
    )
    rid = result.request_id

    client = MagicMock()
    client.issue_get.side_effect = GitHubApiError("missing", status_code=404)
    monkeypatch.setattr(qmod, "GitHubClient", lambda **kwargs: client)
    monkeypatch.setattr(qmod, "QueueStore", lambda **kwargs: store)

    code = qmod.main(["dequeue", "--request-id", rid, "--reason", "ghost issue"])
    assert code == 0

    snap = store.snapshot()
    assert rid not in snap.active_order
    assert rid in snap.completed_request_ids
    assert (snap.request_meta.get(rid) or {}).get("outcome") == "dequeued"
    client.issue_comment.assert_not_called()
    err = capsys.readouterr().err
    assert "404" in err or "not found" in err.lower() or "skip" in err.lower()


def test_dequeue_unknown_request_id_fails(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    monkeypatch.setattr(qmod, "QueueStore", lambda **kwargs: store)

    code = qmod.main(["dequeue", "--request-id", "does-not-exist", "--reason", "x"])
    assert code != 0
    snap = store.snapshot()
    assert snap.active_order == []
    assert "does-not-exist" not in snap.completed_request_ids
