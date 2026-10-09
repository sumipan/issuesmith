"""Explicit repair step module for requires violations (#4276).

Runs a single LLM repair cycle when dispatch requires evaluation finds
non-auto-fixable violations.
"""

from __future__ import annotations

import os
import subprocess

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.engine import run_guarded

_REPAIR_ACTIVE_ENV = "ISSUESMITH_REPAIR_ACTIVE"


def _get_previous_commits(worktree_path: str, base_branch: str) -> str:
    """Return git log --oneline for commits ahead of origin/<base>."""
    if not worktree_path:
        return ""
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"origin/{base_branch}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
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

    old_env = os.environ.get(_REPAIR_ACTIVE_ENV)
    os.environ[_REPAIR_ACTIVE_ENV] = "1"
    try:
        rc = run_guarded(
            "implementation",
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

    if rc != 0:
        return StepResult(exit_code=rc)

    origin_id = ctx.repair_step_origin or ""
    origin_step = get_config().steps.get(origin_id)
    if (
        origin_step is not None
        and origin_step.repair.push
        and worktree_path
    ):
        try:
            push_proc = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                cwd=worktree_path,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary="repair push failed: timeout",
                ),
            )
        if push_proc.returncode != 0:
            err_tail = (push_proc.stderr or push_proc.stdout or "").strip()
            if len(err_tail) > 500:
                err_tail = err_tail[-500:]
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary=f"repair push failed: {err_tail}",
                ),
            )

    return StepResult(status="done")
