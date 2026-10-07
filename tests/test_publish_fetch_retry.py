"""P3 publish: transient ``git fetch`` failures are retried and diagnosable (sumipan/nexus#4747)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.ops import publish as publish_mod
from issuesmith.ops.publish import PublishResult, _ensure_rebased

_WT = Path("/tmp/fake-wt")


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(publish_mod.time, "sleep", sleeps.append)
    return sleeps


def _git_stub(fetch_results: list, ancestor_rc: int = 0):
    calls: list[tuple[str, ...]] = []

    def _run(wt, *args, check=True):
        calls.append(args)
        if args[:2] == ("fetch", "origin"):
            item = fetch_results.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if args[:2] == ("merge-base", "--is-ancestor"):
            return MagicMock(returncode=ancestor_rc, stdout="", stderr="")
        raise AssertionError(f"unexpected git args: {args}")

    return _run, calls


def _fetch_calls(calls):
    return [c for c in calls if c[:2] == ("fetch", "origin")]


def test_fetch_retries_then_reaches_ancestor_check(_no_sleep):
    run, calls = _git_stub([
        MagicMock(returncode=1, stdout="", stderr="error: cannot lock ref 'refs/remotes/origin/main'"),
        MagicMock(returncode=0, stdout="", stderr=""),
    ])
    with patch("issuesmith.ops.publish._run_git", side_effect=run):
        assert _ensure_rebased(_WT, "main") is None
    assert len(_fetch_calls(calls)) == 2
    assert calls[-1][:2] == ("merge-base", "--is-ancestor")
    assert len(_no_sleep) == 1


def test_fetch_called_process_error_is_retried():
    err = subprocess.CalledProcessError(
        1, ["git", "fetch", "origin", "main"], output="", stderr="fatal: unable to access"
    )
    run, calls = _git_stub([err, MagicMock(returncode=0, stdout="", stderr="")])
    with patch("issuesmith.ops.publish._run_git", side_effect=run):
        assert _ensure_rebased(_WT, "main") is None
    assert len(_fetch_calls(calls)) == 2
    assert calls[-1][:2] == ("merge-base", "--is-ancestor")


def test_fetch_exhausted_returns_fetch_failed_with_git_stderr(_no_sleep):
    attempts = len(publish_mod._FETCH_RETRY_DELAYS) + 1
    run, calls = _git_stub([
        MagicMock(returncode=1, stdout="", stderr=f"error: cannot lock ref (try {i})\nfatal: fetch failed")
        for i in range(attempts)
    ])
    with patch("issuesmith.ops.publish._run_git", side_effect=run):
        result = _ensure_rebased(_WT, "main")
    assert isinstance(result, PublishResult)
    assert result.status == "FETCH_FAILED"
    assert result.exit_code == 1
    assert "cannot lock ref" in result.stderr
    assert "fatal:" in result.stderr
    assert f"try {attempts - 1}" in result.stderr  # the last attempt's stderr
    assert len(_fetch_calls(calls)) == attempts == 3
    assert len(_no_sleep) == attempts - 1
    assert not any(c[:2] == ("merge-base", "--is-ancestor") for c in calls)


def test_fetch_failed_falls_back_to_stdout_when_stderr_empty():
    run, _ = _git_stub([
        MagicMock(returncode=1, stdout="remote: temporarily unavailable", stderr="")
        for _ in range(len(publish_mod._FETCH_RETRY_DELAYS) + 1)
    ])
    with patch("issuesmith.ops.publish._run_git", side_effect=run):
        result = _ensure_rebased(_WT, "main")
    assert result is not None
    assert result.status == "FETCH_FAILED"
    assert "remote: temporarily unavailable" in result.stderr


def test_fetch_failed_falls_back_to_command_and_returncode():
    run, _ = _git_stub([
        MagicMock(returncode=128, stdout="", stderr="")
        for _ in range(len(publish_mod._FETCH_RETRY_DELAYS) + 1)
    ])
    with patch("issuesmith.ops.publish._run_git", side_effect=run):
        result = _ensure_rebased(_WT, "main")
    assert result is not None
    assert result.status == "FETCH_FAILED"
    assert result.exit_code == 1
    assert "git fetch origin main" in result.stderr
    assert "128" in result.stderr


def test_main_prints_called_process_error_stderr(capsys):
    err = subprocess.CalledProcessError(
        1, ["git", "-C", "/tmp/wt", "fetch", "origin", "main"], output="", stderr="fatal: fetch failed"
    )
    argv = [
        "--issue", "1",
        "--branch", "feat/x",
        "--base", "main",
        "--worktree", "/tmp/wt",
        "--repo", "o/r",
        "--issue-repo", "o/n",
    ]
    with patch("issuesmith.ops.publish.publish", side_effect=err):
        rc = publish_mod.main(argv)
    assert rc == 1
    captured = capsys.readouterr()
    assert "fatal: fetch failed" in captured.err
    assert "fetch origin main" in captured.err
