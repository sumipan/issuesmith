"""issuesmith doctor — requires_chain validation for StepConfig.requires / input_kind."""

from __future__ import annotations

from typing import Mapping

from issuesmith.config import StepConfig


def validate_requires_chain(steps: Mapping[str, StepConfig]) -> list[str]:
    """Return a list of violation messages for steps that are missing requires.

    Does not re-validate gate ids or input_kind (config loading does that at
    parse time). Only checks that every step has a non-empty requires declaration.
    """
    violations: list[str] = []
    for step_id, step in steps.items():
        if not step.requires:
            violations.append(
                f"steps.{step_id}: missing requires declaration"
            )
    return violations


def requires_chain_report(steps: Mapping[str, StepConfig]) -> str:
    """Return the requires_chain doctor line: 'requires_chain: ok' or a violation summary."""
    violations = validate_requires_chain(steps)
    if not violations:
        return "requires_chain: ok"
    return "requires_chain: " + "; ".join(violations)
