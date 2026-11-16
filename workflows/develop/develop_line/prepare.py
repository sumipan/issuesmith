"""Prepare a git worktree for the reference develop line."""

from __future__ import annotations

import subprocess
from pathlib import Path

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


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    """Create or verify the worktree at ``ctx.worktree_path``."""
    _ = step
    try:
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
