"""Reusable git worktree helpers for issuesmith consumers (#4274).

Public API for base ref resolution, branch validation, fetch retry, worktree
preparation, and cross-repo clone-if-missing. Does not reference workflow
phase names, labels, comments, templates, Slack, diary, or ``jobs/`` paths.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_TRANSIENT_FETCH_MARKERS = ("cannot lock ref", "unable to update local ref")


class WorktreeError(Exception):
    """Deterministic worktree preparation failure."""


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _fail(message: str) -> None:
    print(f"WORKTREE_ERROR: {message}", file=sys.stderr)
    raise WorktreeError(message)


def validate_branch(branch: str) -> None:
    proc = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"invalid branch name: {branch}")


def resolve_base_ref(repo_dir: Path, base: str) -> str:
    """Return the start-point ref for a new branch off *base*.

    ``origin/<base>`` wins so the branch start matches the ``origin/<base>...HEAD``
    range used by pr_scope; a local base may carry unpushed auto-commits
    (nexus #4111). ``refs/heads/<base>`` is only a fallback for local-only repos.
    """
    for ref, result in (
        (f"refs/remotes/origin/{base}", f"origin/{base}"),
        (f"refs/heads/{base}", f"refs/heads/{base}"),
    ):
        found = subprocess.run(
            ["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", ref],
            capture_output=True,
            check=False,
        )
        if found.returncode == 0:
            return result
    raise WorktreeError(f"base branch not found: {base}")


def _is_transient_fetch_error(stderr: str) -> bool:
    return any(marker in stderr for marker in _TRANSIENT_FETCH_MARKERS)


def _lock_wait_seconds() -> int:
    raw = os.environ.get("P0_FETCH_LOCK_WAIT", "30")
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


def fetch_base_with_retry(repo_dir: Path, base: str, *, max_attempts: int = 3) -> bool:
    """Fetch origin base with mkdir lock + transient ref-lock retries.

    Returns ``True`` on success. On exhaustion returns ``False``; callers that
    need a raised error should use :func:`fetch_base_or_raise`.
    """
    lock_dir = repo_dir / ".git" / "issuesmith-fetch.lock"
    lock_wait = _lock_wait_seconds()

    for attempt in range(1, max_attempts + 1):
        waited = 0
        while True:
            try:
                lock_dir.mkdir()
                break
            except FileExistsError:
                waited += 1
                if waited >= lock_wait:
                    break
                _sleep(1)

        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "fetch",
                "origin",
                f"refs/heads/{base}:refs/remotes/origin/{base}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            lock_dir.rmdir()
        except OSError:
            pass

        if proc.returncode == 0:
            return True

        stderr = proc.stderr or ""
        if _is_transient_fetch_error(stderr):
            print(
                f"P0_FETCH_RETRY: attempt={attempt} (transient ref conflict)",
                file=sys.stderr,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_dir),
                    "update-ref",
                    "-d",
                    f"refs/remotes/origin/{base}",
                ],
                capture_output=True,
                check=False,
            )
            _sleep(float(attempt))
            continue

        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
        return False

    return False


def fetch_base_or_raise(repo_dir: Path, base: str, *, remote: str = "origin") -> None:
    """Fetch *base* from *remote*, raising :class:`WorktreeError` after retry exhaustion."""
    if fetch_base_with_retry(repo_dir, base):
        return
    raise WorktreeError(f"failed to fetch {remote}:{base}")


def prepare_worktree(
    repo_dir: Path,
    worktree_dir: Path,
    branch: str,
    base_ref: str,
) -> None:
    if worktree_dir.exists():
        if not worktree_dir.is_dir():
            _fail(f"worktree path is not a directory: {worktree_dir}")
        inside = subprocess.run(
            ["git", "-C", str(worktree_dir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if inside.returncode != 0:
            _fail(f"existing path is not a git worktree: {worktree_dir}")

        expected = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        actual = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_dir),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if expected.returncode != 0 or actual.returncode != 0:
            _fail(f"failed to resolve git-common-dir for worktree: {worktree_dir}")
        if expected.stdout.strip() != actual.stdout.strip():
            _fail(f"existing worktree belongs to a different repository: {worktree_dir}")

        actual_branch = subprocess.run(
            ["git", "-C", str(worktree_dir), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        got = (actual_branch.stdout or "").strip()
        if got != branch:
            _fail(f"existing worktree branch mismatch: expected={branch} actual={got}")
        return

    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    has_branch = subprocess.run(
        ["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        check=False,
    )
    if has_branch.returncode == 0:
        add = subprocess.run(
            ["git", "-C", str(repo_dir), "worktree", "add", str(worktree_dir), branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if add.returncode != 0:
            _fail(f"failed to attach existing branch: {branch}")
        return

    add = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_dir),
            base_ref,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        detail = (add.stderr or add.stdout or "").strip()
        msg = f"failed to create worktree: {worktree_dir}"
        if detail:
            msg = f"{msg}: {detail}"
        _fail(msg)


def clone_if_missing(clone_path: Path, target_repo: str, base_branch: str) -> None:
    """Clone *target_repo* at *base_branch* when *clone_path* does not exist.

    An existing checkout is reused without re-cloning.
    """
    if clone_path.exists():
        return

    clone_path.parent.mkdir(parents=True, exist_ok=True)
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            base_branch,
            f"https://github.com/{target_repo}.git",
            str(clone_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        if clone.stderr:
            print(clone.stderr, file=sys.stderr, end="")
        raise WorktreeError(f"failed to clone {target_repo}")


def _assert_git_repository(repo_dir: Path) -> None:
    git_dir = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    if git_dir.returncode != 0:
        _fail(f"target clone is not a git repository: {repo_dir}")


def _assert_remote_base(repo_dir: Path, base: str) -> None:
    remote_ok = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/remotes/origin/{base}",
        ],
        capture_output=True,
        check=False,
    )
    if remote_ok.returncode != 0:
        _fail(f"fetched base ref not found: origin/{base}")


def prepare_local_worktree(
    repo_dir: Path,
    worktree_path: Path,
    branch: str,
    base_branch: str,
) -> Path:
    """Fetch base (when remote exists), prepare *worktree_path*, return its path."""
    validate_branch(branch)
    validate_branch(base_branch)
    base_ref = resolve_base_ref(repo_dir, base_branch)
    prepare_worktree(repo_dir, worktree_path, branch, base_ref)
    return worktree_path


def prepare_cross_repo_worktree(
    clone_path: Path,
    worktree_path: Path,
    *,
    target_repo: str,
    base_branch: str,
    branch: str,
) -> Path:
    """Clone-if-missing, fetch base, and prepare *worktree_path* for *branch*."""
    validate_branch(branch)
    validate_branch(base_branch)
    clone_if_missing(clone_path, target_repo, base_branch)
    _assert_git_repository(clone_path)
    fetch_base_or_raise(clone_path, base_branch, remote=target_repo)
    _assert_remote_base(clone_path, base_branch)
    prepare_worktree(clone_path, worktree_path, branch, f"origin/{base_branch}")
    return worktree_path


__all__ = [
    "WorktreeError",
    "clone_if_missing",
    "fetch_base_or_raise",
    "fetch_base_with_retry",
    "prepare_cross_repo_worktree",
    "prepare_local_worktree",
    "prepare_worktree",
    "resolve_base_ref",
    "validate_branch",
]
