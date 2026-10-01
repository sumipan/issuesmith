"""cleanup_worktrees / cleanup_branches / close_issue verbs."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ghdag.forge import ForgePort


def _git(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def _list_worktrees(repo_cwd: Path) -> list[Path]:
    proc = _git(["git", "worktree", "list", "--porcelain"], cwd=repo_cwd)
    if proc.returncode != 0:
        return []
    worktrees: list[Path] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            worktrees.append(Path(line.split(" ", 1)[1]))
    return worktrees


def _remove_worktree(repo_cwd: Path, worktree: Path) -> None:
    removed = _git(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_cwd)
    if removed.returncode == 0:
        print(f"CLEANUP: removed worktree {worktree}")
    else:
        print(f"CLEANUP: skip worktree {worktree}")


def _cleanup_branches(repo_cwd: Path, issue_number: str, *, external: bool = False) -> None:
    proc = _git(
        [
            "git",
            "branch",
            "--list",
            f"feat/issue-{issue_number}-*",
            f"docs/issue-{issue_number}-*",
            f"issuesmith/issue-{issue_number}-*",
        ],
        cwd=repo_cwd,
    )
    if proc.returncode != 0:
        return
    prefix = "external " if external else ""
    for line in proc.stdout.splitlines():
        branch = line.lstrip("* ").strip()
        if not branch:
            continue
        deleted = _git(["git", "branch", "-D", branch], cwd=repo_cwd)
        if deleted.returncode == 0:
            print(f"CLEANUP: removed {prefix}branch {branch}")


def _close_issue_if_open(client: ForgePort, issue_number: int) -> None:
    state = client.issue_get(issue_number, fields=["state"])["state"]
    if state == "OPEN":
        client.issue_close(issue_number)
        print(f"FINALIZER: closed issue {issue_number}")
    else:
        print(f"FINALIZER: issue {issue_number} already {state} (noop close)")


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
