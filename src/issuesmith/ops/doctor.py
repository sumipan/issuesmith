"""issuesmith doctor — requires_chain validation for StepConfig.requires / input_kind."""

from __future__ import annotations

from typing import Mapping

from issuesmith.config import StepConfig

# Step IDs that are exempt from the "must have requires" check.
_REPAIR_EXEMPT_IDS: frozenset[str] = frozenset({"repair"})


def validate_requires_chain(steps: Mapping[str, StepConfig]) -> list[str]:
    """Return a list of violation messages for steps that are missing requires.

    Uses requires_declared to distinguish 'requires: []' (explicit empty) from
    a missing requires key. repair is exempt from this check.
    """
    from issuesmith.gates import GATE_REGISTRY

    violations: list[str] = []
    for step_id, step in steps.items():
        if step_id in _REPAIR_EXEMPT_IDS:
            continue
        declared = getattr(step, "requires_declared", False)
        if not declared and not step.requires:
            violations.append(
                f"steps.{step_id}: missing requires declaration"
            )
            continue
        missing = [g for g in step.requires if g not in GATE_REGISTRY]
        if missing:
            violations.append(
                f"steps.{step_id}: missing gate ids: {missing}"
            )
    return violations


def requires_chain_report(steps: Mapping[str, StepConfig]) -> str:
    """Return the requires_chain doctor line: 'requires_chain: ok' or a violation summary."""
    violations = list(validate_requires_chain(steps))
    try:
        from issuesmith.gates import validate_step_requires  # noqa: PLC0415

        validate_step_requires(steps)
    except Exception as exc:  # noqa: BLE001 - ConfigError or an import failure in the registry
        violations.append(str(exc))
    if not violations:
        return "requires_chain: ok"
    return "requires_chain: " + "; ".join(violations)
