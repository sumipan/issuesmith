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
        from issuesmith.config import ConfigError
        from issuesmith.gates import resolve_gate

        missing: list[str] = []
        for g in step.requires:
            try:
                resolve_gate(g)
            except ConfigError:
                missing.append(g)
        if missing:
            violations.append(
                f"steps.{step_id}: missing gate ids: {missing}"
            )
    return violations


def advance_when_report(phases: tuple) -> str:
    """Return advance_when doctor line: ok or a semicolon-separated failure summary."""
    from issuesmith.config import ConfigError
    from issuesmith.preconditions import resolve_predicate

    failures: list[str] = []
    for phase in phases:
        for pred in phase.advance_when:
            try:
                resolve_predicate(pred)
            except ConfigError as exc:
                failures.append(str(exc))
    if not failures:
        return "advance_when: ok"
    return "advance_when: " + "; ".join(failures)


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
