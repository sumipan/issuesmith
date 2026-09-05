"""tests/tools/issuesmith/test_queue_skip.py — skip サブコマンドの単体テスト (#2808)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from ghdag.core.exceptions import GitHubApiError

from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _cli_paths(tmp_path: Path) -> list[str]:
    return [
        "--queue-path",
        str(tmp_path / "queue.jsonl"),
        "--state-path",
        str(tmp_path / "state.json"),
        "--lock-path",
        str(tmp_path / "lock"),
    ]


def test_skip_clears_last_issue_and_comments(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(2297)
    store.set_halt(True, "previous issue not merge-done")

    client = MagicMock()
    client.issue_get.return_value = {"number": 2297, "state": "OPEN"}
    monkeypatch.setattr(qmod, "GitHubClient", lambda **kwargs: client)

    code = qmod.main(
        [
            *_cli_paths(tmp_path),
            "skip",
            "--issue",
            "2297",
            "--reason",
            "abandoned after B1 halt",
        ]
    )
    assert code == 0

    snap = store.snapshot()
    assert snap.last_issue is None
    assert snap.halt is False
    assert snap.halt_reason is None

    out = capsys.readouterr().out
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload == {
        "issue": 2297,
        "outcome": "skipped",
        "reason": "abandoned after B1 halt",
    }

    client.issue_get.assert_called()
    comment_bodies = [c.args[1] for c in client.issue_comment.call_args_list]
    assert any(
        "skipped issue #2297" in body and "abandoned after B1 halt" in body
        for body in comment_bodies
    )


def test_skip_without_halt_only_clears_last_issue(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(100)

    client = MagicMock()
    client.issue_get.return_value = {"number": 100, "state": "OPEN"}
    monkeypatch.setattr(qmod, "GitHubClient", lambda **kwargs: client)

    code = qmod.main([*_cli_paths(tmp_path), "skip", "--issue", "100"])
    assert code == 0

    snap = store.snapshot()
    assert snap.last_issue is None
    assert snap.halt is False

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["issue"] == 100
    assert payload["outcome"] == "skipped"
    assert payload["reason"] == ""


def test_skip_no_last_issue_fails(tmp_path, capsys):
    from issuesmith import queue as qmod

    _store(tmp_path)  # empty state
    code = qmod.main([*_cli_paths(tmp_path), "skip", "--issue", "1"])
    assert code == 1
    err = capsys.readouterr().err
    assert "no last_issue to skip" in err


def test_skip_mismatched_issue_fails(tmp_path, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(2297)

    code = qmod.main([*_cli_paths(tmp_path), "skip", "--issue", "2808"])
    assert code == 1
    err = capsys.readouterr().err
    assert "last_issue is #2297, not #2808" in err

    snap = store.snapshot()
    assert snap.last_issue == 2297


def test_skip_skips_comment_on_404(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(12345)
    store.set_halt(True, "ghost")

    client = MagicMock()
    client.issue_get.side_effect = GitHubApiError("missing", status_code=404)
    monkeypatch.setattr(qmod, "GitHubClient", lambda **kwargs: client)

    code = qmod.main(
        [*_cli_paths(tmp_path), "skip", "--issue", "12345", "--reason", "gone"]
    )
    assert code == 0

    snap = store.snapshot()
    assert snap.last_issue is None
    assert snap.halt is False
    client.issue_comment.assert_not_called()
    err = capsys.readouterr().err
    assert "404" in err or "not found" in err.lower() or "skip" in err.lower()
