"""Prepare a git worktree for the reference develop line."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.config import get_config
from issuesmith.contract import Andon, StepConfig, StepContext, StepResult
from issuesmith.verbs import WorktreeError, prepare_worktree


def _repo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise WorktreeError(proc.stderr.strip() or "not a git repository")
    return Path(proc.stdout.strip())


def _abs_under_config(path: Path) -> Path:
    if path.is_absolute():
        return path
    return get_config().root / path


def _cross_repo(ctx: StepContext) -> bool:
    if get_config().host_is_repo_checkout:
        return ctx.is_cross_repo == "true"
    return True


def _target_clone(ctx: StepContext) -> Path:
    if ctx.target_clone_path.strip():
        return _abs_under_config(Path(ctx.target_clone_path))
    target = ctx.target_repo.strip()
    repo_tail = target.split("/")[-1] if target else ""
    if not repo_tail:
        raise WorktreeError("target_clone_path is missing for cross-repo prepare")
    external = get_config().paths.external_dir
    return _abs_under_config(Path(external) / repo_tail)


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    """Create or verify the worktree at ``ctx.worktree_path``."""
    _ = step
    try:
        if _cross_repo(ctx):
            clone = _target_clone(ctx)
            worktree = _abs_under_config(Path(ctx.worktree_path))
            prepare_worktree(
                clone,
                ctx.base_branch,
                ctx.branch,
                worktree,
                external_dir=clone,
                target_repo=ctx.target_repo,
            )
        else:
            repo = _repo_root()
            prepare_worktree(
                repo,
                ctx.base_branch,
                ctx.branch,
                Path(ctx.worktree_path),
            )
    except WorktreeError as exc:
        return StepResult(status="andon", andon=Andon(kind="broken", summary=str(exc)))
    return StepResult(status="done", markers=["PREPARE_DONE"])
