"""Repair step — runs a single LLM repair cycle for requires violations (#3671)."""

from __future__ import annotations

import os
import subprocess

from issuesmith.config import StepConfig, get_config
from issuesmith.engine import run_guarded
from issuesmith.steps.base import Andon, StepContext, StepResult

_REPAIR_ACTIVE_ENV = "ISSUESMITH_REPAIR_ACTIVE"


def _get_previous_commits(worktree_path: str, base_branch: str) -> str:
    """Return git log --oneline for commits ahead of origin/<base>."""
    if not worktree_path:
        return ""
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"origin/{base_branch}..HEAD"],
            capture_output=True, text=True, check=False,
            cwd=worktree_path,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute one LLM repair cycle.

    Returns andon(broken) when repair_violations is empty or repair is re-entered.
    Raises RetrySignal unchanged (not counted as a repair cycle).
    """
    if os.environ.get(_REPAIR_ACTIVE_ENV):
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                summary="repair step re-entered itself (ISSUESMITH_REPAIR_ACTIVE is set)",
            ),
        )

    if not ctx.repair_violations.strip():
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary="repair launched without violations"),
        )

    if step is None or not step.template:
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary="repair step has no template configured"),
        )

    template = str(get_config().paths.template_dir / step.template)
    worktree_path = ctx.worktree_path or ctx.target_worktree_path or ""

    previous_commits = _get_previous_commits(worktree_path, ctx.base_branch)
    variables = [
        f"repair_violations={ctx.repair_violations}",
        f"repair_step_origin={ctx.repair_step_origin}",
        f"issue_number={ctx.issue_number}",
        f"worktree_path={worktree_path}",
        f"branch={ctx.branch}",
        f"base_branch={ctx.base_branch}",
        f"previous_commits={previous_commits}",
    ]

    env = os.environ.copy()
    env[_REPAIR_ACTIVE_ENV] = "1"
    old_env = os.environ.get(_REPAIR_ACTIVE_ENV)
    os.environ[_REPAIR_ACTIVE_ENV] = "1"
    try:
        rc = run_guarded(
            "implementation",  # engine state roles are "design" / "implementation" (#3794 follow-up)
            template,
            variables,
            success_statuses=["REPAIR_DONE"],
            failure_status="REPAIR_FAILED",
            cwd=worktree_path or None,
        )
    finally:
        if old_env is None:
            os.environ.pop(_REPAIR_ACTIVE_ENV, None)
        else:
            os.environ[_REPAIR_ACTIVE_ENV] = old_env

    if rc == 0:
        return StepResult(status="done")
    return StepResult(exit_code=rc)
