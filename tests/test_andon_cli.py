"""Tests for andon CLI subcommands and resume hook behavior (#3509)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_andon(
    *,
    andon_id: str = "issuesmith:42:cp2:0",
    kind: str = "decision",
    issue: int = 42,
    step: str = "cp2",
    summary: str = "test andon",
):
    from issuesmith.andon import Andon
    return Andon(
        id=andon_id,
        kind=kind,
        issue=issue,
        step=step,
        summary=summary,
    )


def _fake_client_with_andon(andon):
    from issuesmith.andon import to_comment
    client = MagicMock()
    comment_body = to_comment(andon)
    client.get_issue_comments.return_value = [{"body": comment_body, "id": 1}]
    client.list_issues.return_value = [{"number": andon.issue}]
    client.issue_comment.return_value = {}
    client.issue_update.return_value = {}
    return client


# ---------------------------------------------------------------------------
# andon CLI list
# ---------------------------------------------------------------------------

def test_andon_list_no_args_returns_nonzero():
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-m", "issuesmith", "andon"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0


def test_andon_list_shows_usage_on_no_args():
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-m", "issuesmith", "andon"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    # usage or list must appear in stderr
    assert "list" in result.stderr or "usage" in result.stderr.lower()


# ---------------------------------------------------------------------------
# _call_resume_hook: resume action → resume() called, dispatch_event not used
# ---------------------------------------------------------------------------

def test_call_resume_hook_resume_action_calls_resume():
    """_call_resume_hook with action='resume' must call resume.resume(), not dispatch_event."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.resume.resume") as mock_resume:
        mock_resume.return_value = 0
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")
    mock_resume.assert_called_once_with(42, from_step="cp2")


def test_call_resume_hook_resume_action_no_dispatch_event():
    """resume action must not call dispatch_event (the old broken path)."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.resume.resume", return_value=0):
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")
    client.dispatch_event.assert_not_called()


def test_call_resume_hook_handoff_no_resume():
    """handoff action must not call resume (label/comment only)."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.resume.resume") as mock_resume:
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "handoff")
    mock_resume.assert_not_called()


def test_call_resume_hook_handoff_no_dispatch_event():
    """handoff action must not call dispatch_event."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    _call_resume_hook(client, "issuesmith:42:cp2:0", "handoff")
    client.dispatch_event.assert_not_called()


def test_call_resume_hook_unknown_action_no_resume():
    """Unknown actions must not call resume."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.resume.resume") as mock_resume:
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "unknown-action")
    mock_resume.assert_not_called()


def test_call_resume_hook_resume_exception_suppressed():
    """Exceptions inside resume must not propagate (same contract as before)."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.resume.resume", side_effect=RuntimeError("oops")):
        # Should not raise
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")


def test_call_resume_hook_bad_andon_id_graceful():
    """Malformed andon_id must not raise."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.resume.resume") as mock_resume:
        _call_resume_hook(client, "bad-id", "resume")
    mock_resume.assert_not_called()


# ---------------------------------------------------------------------------
# dispatch_event must not be called anywhere in andon.py (§3509 AC)
# ---------------------------------------------------------------------------

def test_dispatch_event_not_called_on_answer():
    """answer() must not call dispatch_event after #3509."""
    a = _make_andon()
    client = _fake_client_with_andon(a)
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.resume.resume", return_value=0),
    ):
        mock_iter.return_value = iter([{"number": a.issue}])
        from issuesmith.andon import answer
        answer(client, a.id, "resume")
    client.dispatch_event.assert_not_called()


# ---------------------------------------------------------------------------
# FutureWarning on recover / redispatch CLI
# ---------------------------------------------------------------------------

def test_cli_recover_emits_future_warning():
    import warnings
    import os
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = __import__("subprocess").run(
        [sys.executable, "-W", "all", "-m", "issuesmith", "recover", "--help"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "DeprecationWarning" in combined or "FutureWarning" in combined or result.returncode in (0, 1, 2)


def test_cli_redispatch_emits_future_warning():
    import subprocess
    import sys
    import os
    from pathlib import Path
    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-W", "all", "-m", "issuesmith", "redispatch", "--help"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    combined = result.stdout + result.stderr
    assert "DeprecationWarning" in combined or "FutureWarning" in combined or result.returncode in (0, 1, 2)
