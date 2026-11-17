"""Verify step placeholder; gates run via dispatch requires."""

from __future__ import annotations

from issuesmith.config import StepConfig
from issuesmith.contract import StepContext, StepResult


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    _ = ctx, step
    return StepResult(status="done", markers=["VERIFY_DONE"])
