"""Deprecated compat re-exports — import from issuesmith.ops.repair_step instead (#4276)."""

from __future__ import annotations

import warnings

from issuesmith.config import StepConfig
from issuesmith.contract import StepContext, StepResult
from issuesmith.ops.repair_step import run as _run

__all__ = ["run"]

warnings.warn(
    "issuesmith.steps.repair is deprecated; use issuesmith.ops.repair_step instead",
    DeprecationWarning,
    stacklevel=2,
)


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    return _run(ctx, step)
