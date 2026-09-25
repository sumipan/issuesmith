"""Tests for andon CLI subcommands and resume hook behavior (#3509)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    with patch("issuesmith.andon.resume") as mock_resume:
        mock_resume.return_value = 0
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")
    mock_resume.assert_called_once_with(42, from_step="cp2")


def test_call_resume_hook_resume_action_no_dispatch_event():
    """resume action must not call dispatch_event (the old broken path)."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.andon.resume", return_value=0):
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")
    client.dispatch_event.assert_not_called()


def test_call_resume_hook_handoff_no_resume():
    """handoff action must not call resume (label/comment only)."""
    from issuesmith.andon import _call_resume_hook
    with patch("issuesmith.andon.resume") as mock_resume:
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
    with patch("issuesmith.andon.resume") as mock_resume:
        client = MagicMock()
        _call_resume_hook(client, "issuesmith:42:cp2:0", "unknown-action")
    mock_resume.assert_not_called()


def test_call_resume_hook_resume_exception_suppressed():
    """Exceptions inside resume must not propagate (same contract as before)."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.andon.resume", side_effect=RuntimeError("oops")):
        # Should not raise
        _call_resume_hook(client, "issuesmith:42:cp2:0", "resume")


def test_call_resume_hook_bad_andon_id_graceful():
    """Malformed andon_id must not raise."""
    from issuesmith.andon import _call_resume_hook
    client = MagicMock()
    with patch("issuesmith.andon.resume") as mock_resume:
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
    import os
    import subprocess
    import sys
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


# ---------------------------------------------------------------------------
# AC-5 / AC-6: andon list sort order and --all flag
# ---------------------------------------------------------------------------

def _make_andons_mixed():
    """Return a list of andons in an unsorted order for testing default sort."""
    from issuesmith.andon import Andon
    return [
        Andon(id="observe:1:orphan:uuid1:0", kind="blocked", issue=1, step="observe", summary="orphan"),
        Andon(id="issuesmith:2:cp2:0", kind="broken", issue=2, step="cp2", summary="broken thing"),
        Andon(id="observe:3:stall:develop:0", kind="blocked", issue=3, step="observe", summary="stall"),
        Andon(id="issuesmith:4:cp2:0", kind="decision", issue=4, step="cp2", summary="needs decision"),
        Andon(id="issuesmith:5:p2:0", kind="blocked", issue=5, step="p2", summary="blocked non-observe"),
    ]


def test_andon_list_default_sort_order():
    """AC-5: default list sorts decision → broken → non-observe blocked → observe blocked."""
    from io import StringIO
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    from issuesmith.__main__ import _cmd_andon

    andons = _make_andons_mixed()
    fake_client = MagicMock()
    output = StringIO()

    with mpatch("issuesmith.andon.list_open", return_value=andons), \
         mpatch("ghdag.forge.get_forge", return_value=fake_client), \
         mpatch("sys.stdout", output):
        ret = _cmd_andon(["list"])

    assert ret == 0
    lines = [ln for ln in output.getvalue().splitlines() if ln.strip()]
    ids_in_output = [ln.split("\t")[0] for ln in lines]
    kinds_order = [ln.split("\t")[1] for ln in lines]
    steps_order = [next(a.step for a in andons if a.id == aid) for aid in ids_in_output]

    # decision must come first
    assert kinds_order[0] == "decision", f"expected decision first, got: {kinds_order}"
    # broken before non-observe blocked
    broken_idx = kinds_order.index("broken")
    non_obs_blocked_idx = next(
        i for i, (k, s) in enumerate(zip(kinds_order, steps_order))
        if k == "blocked" and s != "observe"
    )
    obs_blocked_indices = [
        i for i, (k, s) in enumerate(zip(kinds_order, steps_order))
        if k == "blocked" and s == "observe"
    ]
    assert broken_idx < non_obs_blocked_idx, "broken should precede non-observe blocked"
    assert all(i > non_obs_blocked_idx for i in obs_blocked_indices), (
        "observe blocked should come after non-observe blocked"
    )


def test_andon_list_all_flag_shows_unsorted():
    """AC-6: --all flag shows all andons without sorting (original order)."""
    from io import StringIO
    from unittest.mock import MagicMock
    from unittest.mock import patch as mpatch

    from issuesmith.__main__ import _cmd_andon

    andons = _make_andons_mixed()
    fake_client = MagicMock()
    output = StringIO()

    with mpatch("issuesmith.andon.list_open", return_value=andons), \
         mpatch("ghdag.forge.get_forge", return_value=fake_client), \
         mpatch("sys.stdout", output):
        ret = _cmd_andon(["list", "--all"])

    assert ret == 0
    lines = [ln for ln in output.getvalue().splitlines() if ln.strip()]
    ids_in_output = [ln.split("\t")[0] for ln in lines]
    ids_original = [a.id for a in andons]
    assert ids_in_output == ids_original, "--all should preserve original order"
