"""Review step placeholder; the review gate runs the LLM."""

from __future__ import annotations

from issuesmith.config import StepConfig
from issuesmith.contract import StepContext, StepResult


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    _ = ctx, step
    return StepResult(status="done", markers=["REVIEW_STEP_DONE"])
