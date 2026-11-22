"""Configurable version-bump policies for publish (#7789)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

from issuesmith.bump_commits import BUMP_SUBJECT_PREFIX, is_bump_only_pyproject_change
from issuesmith.config import get_config

ResolvedPolicy = Literal["none", "pyproject", "command"]

_PYPROJECT_BUMP_PATHS = frozenset({"pyproject.toml", "CHANGELOG.md"})


def resolve_policy(worktree: Path) -> ResolvedPolicy:
    """Resolve ``version.policy`` for *worktree* (``auto`` → ``pyproject`` or ``none``)."""
    raw = get_config().version.policy
    if raw == "auto":
        if (Path(worktree) / "pyproject.toml").is_file():
            return "pyproject"
        return "none"
    if raw == "none":
        return "none"
    if raw == "pyproject":
        return "pyproject"
    return "command"


def _run_git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _token_from_command_stdout(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        return parts[0] if parts else "unknown"
    return "unknown"


def run_bump(worktree: Path, base_ref: str) -> subprocess.CompletedProcess[str]:
    """Run the configured bump for *worktree* against *base_ref*."""
    worktree = Path(worktree)
    policy = resolve_policy(worktree)
    if policy == "none":
        return subprocess.CompletedProcess([], 0, "", "")

    if policy == "pyproject":
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "issuesmith",
                "version-bump",
                "--worktree",
                str(worktree),
                "--base",
                base_ref,
            ],
            capture_output=True,
            text=True,
        )

    command = get_config().version.bump_command
    proc = subprocess.run(
        list(command),
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return proc

    _run_git(worktree, "add", "-u")
    staged = _run_git(worktree, "diff", "--cached", "--name-only")
    if not staged.stdout.strip():
        return subprocess.CompletedProcess(list(command), 0, proc.stdout, proc.stderr)

    token = _token_from_command_stdout(proc.stdout)
    subject = f"{BUMP_SUBJECT_PREFIX}{token} (command)"
    commit = _run_git(worktree, "commit", "-m", subject)
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout or "").strip()
        return subprocess.CompletedProcess(
            list(command),
            commit.returncode,
            proc.stdout,
            detail or proc.stderr,
        )
    return subprocess.CompletedProcess(list(command), 0, proc.stdout, proc.stderr)


def _files_touched_by_bump_commits(worktree: Path, base_branch: str) -> frozenset[str]:
    log = _run_git(
        worktree,
        "log",
        "--reverse",
        "--format=%H\t%s",
        f"origin/{base_branch}..HEAD",
    )
    paths: set[str] = set()
    for line in log.stdout.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        if not subject.startswith(BUMP_SUBJECT_PREFIX):
            continue
        show = _run_git(worktree, "show", "--name-only", "--format=", sha)
        for rel in show.stdout.splitlines():
            rel = rel.strip()
            if rel:
                paths.add(rel)
    return frozenset(paths)


def bump_paths(worktree: Path, base_branch: str) -> frozenset[str]:
    policy = resolve_policy(worktree)
    if policy == "none":
        return frozenset()
    if policy == "pyproject":
        return _PYPROJECT_BUMP_PATHS
    return _files_touched_by_bump_commits(worktree, base_branch)


def is_bump_only_change(worktree: Path, base_branch: str) -> bool:
    policy = resolve_policy(worktree)
    if policy == "none":
        return False
    if policy == "pyproject":
        return is_bump_only_pyproject_change(worktree, base_branch)

    allowed = bump_paths(worktree, base_branch)
    if not allowed:
        return False

    log = _run_git(
        worktree,
        "log",
        "--format=%H\t%s",
        f"origin/{base_branch}..HEAD",
    )
    for line in log.stdout.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        if subject.startswith(BUMP_SUBJECT_PREFIX):
            continue
        show = _run_git(worktree, "show", "--name-only", "--format=", sha)
        for rel in show.stdout.splitlines():
            rel = rel.strip()
            if rel and rel in allowed:
                return False
    return True


__all__ = [
    "ResolvedPolicy",
    "bump_paths",
    "is_bump_only_change",
    "resolve_policy",
    "run_bump",
]
