"""Acceptance tests for ISSUESMITH_FETCH_TIMEOUT_SEC and fetch failure reasons (#7062)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith import worktree as wt


def _bare_with_clone(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(seed)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (seed / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(seed), "add", "README"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "-q", "origin", "main"],
        check=True,
        capture_output=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(clone),
            "fetch",
            "origin",
            "main:refs/remotes/origin/main",
        ],
        check=True,
        capture_output=True,
    )
    return clone


def test_fetch_exceeding_timeout_raises_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#7062 AC1: sub-second timeout aborts fetch and surfaces reason; lock is not left behind."""
    repo = _bare_with_clone(tmp_path)
    lock_dir = wt._git_common_dir(repo) / "issuesmith-fetch.lock"
    monkeypatch.setenv("ISSUESMITH_FETCH_TIMEOUT_SEC", "0.001")
    monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "0")
    monkeypatch.setattr(wt, "_sleep", lambda _s: None)
    with pytest.raises(wt.WorktreeError, match="failed to fetch sumipan/issuesmith:main: timed out after"):
        wt.fetch_base_or_raise(repo, "main", remote="sumipan/issuesmith")
    assert not lock_dir.exists()


def test_fetch_without_lock_keeps_other_holders_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#7062 AC2: lock wait expiry must not remove another holder's lock."""
    repo = _bare_with_clone(tmp_path)
    lock_dir = wt._git_common_dir(repo) / "issuesmith-fetch.lock"
    lock_dir.mkdir()
    try:
        monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "1")
        monkeypatch.setattr(wt, "_sleep", lambda _s: None)
        assert wt.fetch_base_with_retry(repo, "main") is True
        assert lock_dir.is_dir()
    finally:
        if lock_dir.is_dir():
            lock_dir.rmdir()


def test_fetch_failure_raises_with_git_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """#7062 AC3: fetch errors include the last non-empty git stderr line."""
    repo = _bare_with_clone(tmp_path)
    missing = tmp_path / "missing.git"
    subprocess.run(
        ["git", "-C", str(repo), "remote", "set-url", "origin", str(missing)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "0")
    monkeypatch.setattr(wt, "_sleep", lambda _s: None)
    prefix = "failed to fetch sumipan/issuesmith:main: "
    with pytest.raises(wt.WorktreeError, match=rf"^{prefix}.+") as exc:
        wt.fetch_base_or_raise(repo, "main", remote="sumipan/issuesmith")
    reason = str(exc.value)[len(prefix) :]
    assert reason
    # The reason must come from git's stderr, not the exit-code fallback.
    assert not reason.startswith("git fetch exited")
