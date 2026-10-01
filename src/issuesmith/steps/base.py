"""Deprecated compat re-exports — import from issuesmith.contract instead (#4272)."""

from __future__ import annotations

import warnings

from issuesmith.contract import Andon, StepContext, StepResult, Verdict

__all__ = ["Andon", "StepContext", "StepResult", "Verdict"]

warnings.warn(
    "issuesmith.steps.base is deprecated; "
    "import StepContext, StepResult, Andon, Verdict from issuesmith.contract instead",
    DeprecationWarning,
    stacklevel=2,
)
