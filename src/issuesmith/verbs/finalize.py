"""cleanup_worktrees / cleanup_branches / close_issue verbs — public extraction from steps.m2_finalize."""

from __future__ import annotations

import re
from pathlib import Path

from ghdag.forge import ForgePort

from issuesmith.steps.m2_finalize import (
    _cleanup_branches,
    _close_issue_if_open,
    _list_worktrees,
    _remove_worktree,
)


def cleanup_worktrees(
    repo_cwd: Path,
    issue_number: str,
    *,
    is_cross_repo: bool = False,
    target_clone_path: Path | None = None,
) -> None:
    """Remove worktrees and local branches for *issue_number*.

    When *is_cross_repo* is True and *target_clone_path* is provided, the
    external repo's worktrees and branches are cleaned up as well.
    """
    repo_cwd = Path(repo_cwd)
    pattern = re.compile(rf"/\.claude/worktrees/issue-{re.escape(str(issue_number))}(-|/|$)")
    for worktree in _list_worktrees(repo_cwd):
        if pattern.search(str(worktree)):
            _remove_worktree(repo_cwd, worktree)
    _cleanup_branches(repo_cwd, str(issue_number))

    if not is_cross_repo or target_clone_path is None:
        return
    target_repo = Path(target_clone_path)
    if not target_repo.is_absolute():
        target_repo = repo_cwd / target_repo
    if not (target_repo / ".git").exists():
        return
    ext_pattern = re.compile(rf"/worktrees/issue-{re.escape(str(issue_number))}(-|/|$)")
    for worktree in _list_worktrees(target_repo):
        if ext_pattern.search(str(worktree)):
            _remove_worktree(target_repo, worktree)
    _cleanup_branches(target_repo, str(issue_number), external=True)


def cleanup_branches(repo_cwd: Path, issue_number: str, *, external: bool = False) -> None:
    """Delete local branches matching the issue pattern in *repo_cwd*."""
    _cleanup_branches(Path(repo_cwd), str(issue_number), external=external)


def close_issue(client: ForgePort, issue_number: int) -> None:
    """Close *issue_number* if it is still open."""
    _close_issue_if_open(client, issue_number)


__all__ = ["cleanup_worktrees", "cleanup_branches", "close_issue"]
