"""Run the review LLM with issue body and worktree diff."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.config import StepConfig
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.gates.review import ReviewGate


def _git_diff(worktree: Path, base_branch: str) -> str:
    proc = subprocess.run(
        ["git", "diff", f"origin/{base_branch}...HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return proc.stderr.strip() or "git diff failed"
    return proc.stdout


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    """Populate review gate variables and run the configured review template."""
    review_cfg = step.review
    if review_cfg is None:
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary="review step missing review config"),
        )

    issue = int(ctx.issue_number)
    forge = get_forge()
    issue_body = str(forge.issue_get(issue, fields=["body"]).get("body") or "")
    worktree = Path(ctx.worktree_path)
    diff = _git_diff(worktree, ctx.base_branch)

    gate = ReviewGate(
        review_cfg,
        "review",
        worktree,
        {"issue_body": issue_body, "diff": diff},
    )
    violations = gate.check("", [])
    if not violations:
        return StepResult(status="done", markers=["REVIEW_DONE"])

    engine_errors = [
        v for v in violations if v.rule_id == "review.review.engine_error"
    ]
    kind = "broken" if engine_errors else "blocked"
    summary = violations[0].message if violations else "review failed"
    return StepResult(status="andon", andon=Andon(kind=kind, summary=summary))
