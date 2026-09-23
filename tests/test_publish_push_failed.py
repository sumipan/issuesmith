"""Plain ``git push`` failures are retried and reported as PUSH_FAILED, never raised (sumipan/nexus#3680)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.ops import publish as publish_mod
from issuesmith.ops.publish import _push_branch

_BRANCH = "feat/issue-3680"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", str(remote), str(wt)], check=True, capture_output=True)
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    (wt / "a.txt").write_text("a\n", encoding="utf-8")
    _git(wt, "add", "a.txt")
    _git(wt, "commit", "-m", "seed")
    _git(wt, "push", "-u", "origin", "main")
    _git(wt, "checkout", "-b", _BRANCH)
    (wt / "b.txt").write_text("b\n", encoding="utf-8")
    _git(wt, "add", "b.txt")
    _git(wt, "commit", "-m", "feature")
    return remote, wt


@pytest.fixture(autouse=True)
def _no_delays(monkeypatch):
    monkeypatch.setattr(publish_mod, "_PUSH_RETRY_DELAYS", (0.0, 0.0))


def test_unreachable_remote_returns_push_failed_after_retries(tmp_path: Path, capsys):
    _remote, wt = _setup(tmp_path)
    _git(wt, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = _push_branch(wt, _BRANCH)

    assert result is not None
    assert result.status == "PUSH_FAILED"
    assert result.exit_code == 1
    assert "missing.git" in result.stderr or "does not appear to be a git repository" in result.stderr
    assert "attempt 2/3" in capsys.readouterr().err  # retried, then reported


def test_transient_failure_then_success_returns_none(tmp_path: Path):
    remote, wt = _setup(tmp_path)
    good_url = str(remote)
    _git(wt, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    original = publish_mod._run_git
    pushes = {"n": 0}

    def _run_git_flaky(worktree: Path, *args: str, check: bool = True):
        if args[:1] == ("push",):
            pushes["n"] += 1
            result = original(worktree, *args, check=check)
            if pushes["n"] == 1:
                _git(worktree, "remote", "set-url", "origin", good_url)  # network back
            return result
        return original(worktree, *args, check=check)

    publish_mod._run_git = _run_git_flaky  # type: ignore[assignment]
    try:
        result = _push_branch(wt, _BRANCH)
    finally:
        publish_mod._run_git = original  # type: ignore[assignment]

    assert result is None
    assert pushes["n"] == 2
    assert _git(wt, "rev-parse", f"origin/{_BRANCH}").returncode == 0


def test_rejection_is_not_retried(tmp_path: Path):
    """A remote update that lands after our fetch is a rejection (PUSH_DIVERGED): no retry."""
    remote, wt = _setup(tmp_path)
    _git(wt, "push", "-u", "origin", _BRANCH)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(remote), str(other)], check=True, capture_output=True)
    _git(other, "config", "user.email", "t@t")
    _git(other, "config", "user.name", "t")
    _git(other, "checkout", _BRANCH)
    (wt / "d.txt").write_text("d\n", encoding="utf-8")
    _git(wt, "add", "d.txt")
    _git(wt, "commit", "--amend", "--no-edit")  # rewritten tip: not an ancestor of origin
    original = publish_mod._run_git
    pushes = {"n": 0}

    def _inject(worktree: Path, *args: str, check: bool = True):
        result = original(worktree, *args, check=check)
        if args[:3] == ("fetch", "origin", _BRANCH):
            (other / "c.txt").write_text("c\n", encoding="utf-8")
            _git(other, "add", "c.txt")
            _git(other, "commit", "-m", "unknown remote commit")
            _git(other, "push", "origin", _BRANCH)
        if args[:1] == ("push",):
            pushes["n"] += 1
        return result

    publish_mod._run_git = _inject  # type: ignore[assignment]
    try:
        result = _push_branch(wt, _BRANCH)
    finally:
        publish_mod._run_git = original  # type: ignore[assignment]

    assert result is not None and result.status == "PUSH_DIVERGED"
    assert pushes["n"] == 1
