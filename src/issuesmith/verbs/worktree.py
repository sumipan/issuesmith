"""prepare_worktree verb — public worktree orchestration (#6581 / nexus #6477)."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from ghdag.forge import ForgePort

from issuesmith.contract import StepResult
from issuesmith.worktree import (
    WorktreeError,
    clone_if_missing,
    fetch_base_or_raise,
    handle_milestone,
    resolve_base_ref,
    validate_branch,
)
from issuesmith.worktree import assert_jobs_clean as _assert_jobs_clean
from issuesmith.worktree import ensure_base_included as _ensure_base_included
from issuesmith.worktree import prepare_worktree as _prepare_worktree

__all__ = [
    "WorktreeError",
    "block_design_only_issue",
    "ensure_base_included",
    "prepare_worktree",
    "resolve_base_ref",
]


def _assert_git_repository(repo_dir: Path) -> None:
    git_dir = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    if git_dir.returncode != 0:
        raise WorktreeError(f"target clone is not a git repository: {repo_dir}")


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
        raise WorktreeError(f"fetched base ref not found: origin/{base}")


def _cross_repo_pair_error() -> WorktreeError:
    return WorktreeError(
        "external_dir and target_repo must both be set or both omitted"
    )


def prepare_worktree(
    repo: Path,
    base: str,
    branch: str,
    path: Path,
    *,
    external_dir: Path | None = None,
    target_repo: str | None = None,
    clean_path: str | None = None,
) -> Path:
    """Create or verify a git worktree at *path* branched from *base*.

    When *external_dir* and *target_repo* are both set, clone-if-missing runs on
    *external_dir*. Otherwise *repo* is the repository root.

    Migration (#6581): older ``issuesmith.verbs`` shims accepted ``external_dir`` alone
    and passed it to ``resolve_base_ref`` / ``_prepare_worktree``. Callers must now pass
    ``target_repo`` as well; cross-repo fetch uses *target_repo* as the git remote name
    and ``base_ref`` is ``origin/{base}`` (``resolve_base_ref`` is not used on that path).
    """
    validate_branch(branch)
    validate_branch(base)

    has_external = external_dir is not None
    has_target = target_repo is not None
    if has_external != has_target:
        raise _cross_repo_pair_error()

    if clean_path is not None and Path(clean_path).is_absolute():
        raise WorktreeError("clean_path must be a relative path")

    worktree_path = Path(path)
    cross_clone = has_external and has_target

    if cross_clone:
        assert external_dir is not None
        assert target_repo is not None
        repo_dir = Path(external_dir)
        clone_if_missing(repo_dir, target_repo, base)
        _assert_git_repository(repo_dir)
        fetch_base_or_raise(repo_dir, base, remote=target_repo)
        _assert_remote_base(repo_dir, base)
        base_ref = f"origin/{base}"
    else:
        repo_dir = Path(repo)
        _assert_git_repository(repo_dir)
        fetch_base_or_raise(repo_dir, base)
        base_ref = resolve_base_ref(repo_dir, base)

    _prepare_worktree(repo_dir, worktree_path, branch, base_ref)

    if clean_path is not None:
        _assert_jobs_clean(worktree_path, clean_path)

    return worktree_path.resolve()


def ensure_base_included(
    worktree_dir: Path,
    base_branch: str,
    client: ForgePort,
    issue_number: int,
    *,
    stale_base_comment_template: str,
    caller_step: str | None = None,
) -> StepResult | None:
    """Return ``None`` when HEAD includes origin/*base_branch*; rebase or fail otherwise."""
    return _ensure_base_included(
        worktree_dir,
        base_branch,
        client,
        issue_number,
        stale_base_comment_template=stale_base_comment_template,
        caller_step=caller_step,
    )


def block_design_only_issue(
    client: ForgePort,
    issue_number: int,
    *,
    comment: str,
    on_transition: Callable[[int], None] | None = None,
    stderr_error: str | None = None,
) -> StepResult:
    """Block milestone-scoped issues from entering implementation."""
    kwargs: dict[str, object] = {
        "comment": comment,
        "on_transition": on_transition,
    }
    if stderr_error is not None:
        kwargs["stderr_error"] = stderr_error
    return handle_milestone(client, issue_number, **kwargs)
