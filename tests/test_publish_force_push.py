"""Real-git fixtures for rebase-aware push (#3237).

Reproduces the 2026-09-13 nexus #3232 P3 retry failure:
``_ensure_rebased`` rewrites SHAs after a prior push, so plain
``git push -u origin <branch>`` is rejected as non-fast-forward.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.ops import publish as publish_mod
from issuesmith.ops.publish import _push_branch

_BRANCH = "feat/issue-3237"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _config_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_rebased_after_push(tmp_path: Path) -> Path:
    """bare origin + feature branch: same content, different SHA after rebase.

    Mirrors the #3232 real data: remote has old tip; local HEAD was rebased
    onto an advanced base so ancestry of origin/<branch> is broken.
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )

    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", str(remote), str(seed)],
        check=True,
        capture_output=True,
    )
    _config_identity(seed)
    _write(seed, "README", "base-v1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "clone", str(remote), str(wt)],
        check=True,
        capture_output=True,
    )
    _config_identity(wt)
    _git(wt, "checkout", "-b", _BRANCH)
    _write(wt, "src/feat.py", "feat-v1\n")
    _git(wt, "add", "--", "src/feat.py")
    _git(wt, "commit", "-m", "feat change")
    _git(wt, "push", "-u", "origin", _BRANCH)

    # Advance origin/main, then rebase feature (rewrites SHAs; remote tip unchanged).
    _write(seed, "README", "base-v1\norigin-ahead\n")
    _git(seed, "add", "--", "README")
    _git(seed, "commit", "-m", "origin advances")
    _git(seed, "push", "origin", "main")

    _git(wt, "fetch", "origin", "main")
    rebase = _git(wt, "rebase", "origin/main", check=False)
    assert rebase.returncode == 0, rebase.stderr

    ancestor = _git(
        wt, "merge-base", "--is-ancestor", f"origin/{_BRANCH}", "HEAD", check=False
    )
    assert ancestor.returncode != 0
    return wt


def test_rebase_then_repush_succeeds_with_force_with_lease(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wt = _setup_rebased_after_push(tmp_path)
    local_before = _git(wt, "rev-parse", "HEAD").stdout.strip()

    result = _push_branch(wt, _BRANCH)
    assert result is None

    captured = capsys.readouterr()
    assert "rev-list ahead=" in captured.out
    assert "behind=" in captured.out

    _git(wt, "fetch", "origin", _BRANCH)
    remote_tip = _git(wt, "rev-parse", f"origin/{_BRANCH}").stdout.strip()
    assert remote_tip == local_before


def test_unknown_remote_commit_returns_push_diverged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Simulate another push between fetch and force-with-lease (lease miss)."""
    wt = _setup_rebased_after_push(tmp_path)
    remote = tmp_path / "remote.git"
    branch = _BRANCH

    other = tmp_path / "other"
    subprocess.run(
        ["git", "clone", str(remote), str(other)],
        check=True,
        capture_output=True,
    )
    _config_identity(other)
    _git(other, "checkout", branch)

    original = publish_mod._run_git

    def _run_git_inject(worktree: Path, *args: str, check: bool = True):
        result = original(worktree, *args, check=check)
        if args[:3] == ("fetch", "origin", branch):
            _write(other, "src/other.py", "other-unknown\n")
            _git(other, "add", "--", "src/other.py")
            _git(other, "commit", "-m", "unknown remote commit")
            _git(other, "push", "origin", branch)
        return result

    publish_mod._run_git = _run_git_inject  # type: ignore[assignment]
    try:
        result = _push_branch(wt, branch)
    finally:
        publish_mod._run_git = original  # type: ignore[assignment]

    assert result is not None
    assert result.status == "PUSH_DIVERGED"
    assert result.exit_code == 1
    assert result.stderr

    captured = capsys.readouterr()
    assert "rev-list ahead=" in captured.out


def test_no_remote_branch_pushes_normally(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )

    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "clone", str(remote), str(wt)],
        check=True,
        capture_output=True,
    )
    _config_identity(wt)
    _write(wt, "README", "init\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "init")
    _git(wt, "branch", "-M", "main")
    _git(wt, "push", "-u", "origin", "main")

    _git(wt, "checkout", "-b", _BRANCH)
    _write(wt, "src/feat.py", "feat-v1\n")
    _git(wt, "add", "--", "src/feat.py")
    _git(wt, "commit", "-m", "feat change")

    # Branch never pushed — origin/<branch> must not exist yet.
    missing = _git(wt, "rev-parse", f"origin/{_BRANCH}", check=False)
    assert missing.returncode != 0

    result = _push_branch(wt, _BRANCH)
    assert result is None

    _git(wt, "fetch", "origin", _BRANCH)
    remote_tip = _git(wt, "rev-parse", f"origin/{_BRANCH}").stdout.strip()
    local_tip = _git(wt, "rev-parse", "HEAD").stdout.strip()
    assert remote_tip == local_tip
