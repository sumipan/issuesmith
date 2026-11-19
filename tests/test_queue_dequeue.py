"""F4: tests for the issuesmith.queue dequeue subcommand."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.forge import GitHubApiError

from issuesmith.queue_store import QueueStore

_NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).isoformat()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


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
    monkeypatch.setattr(qmod, "get_forge", lambda **kwargs: client)
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


def test_seed_extra_sources_from_window_yaml(tmp_path):
    """Refs #7329."""
    from issuesmith.queue_triage import seed_extra_sources

    seed_path = tmp_path / "night-queue.yaml"
    seed_path.write_text(
        "window:\n  extra_sources:\n    - release-watcher\n    - custom\n",
        encoding="utf-8",
    )
    assert seed_extra_sources(seed_path) == frozenset({"release-watcher", "custom"})


def test_dequeue_unknown_request_id_fails(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    monkeypatch.setattr(qmod, "QueueStore", lambda **kwargs: store)

    code = qmod.main(["dequeue", "--request-id", "does-not-exist", "--reason", "x"])
    assert code != 0
    snap = store.snapshot()
    assert snap.active_order == []
    assert "does-not-exist" not in snap.completed_request_ids
