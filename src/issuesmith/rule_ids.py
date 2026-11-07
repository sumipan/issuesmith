"""Normalize legacy host rule ids to concept names (#6125)."""

from __future__ import annotations

RULE_ID_ALIASES: dict[str, str] = {
    "cp1": "design_lint",
    "m2": "ac_complete",
    "b1_ac_format": "ac_format",
    "b1_migration": "migration_plan",
    "b1_milestone_subdesign": "milestone_subdesign",
    "b1_verify": "step_verify",
}

_PREFIX_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((old, new) for old, new in RULE_ID_ALIASES.items() if "." not in old),
        key=lambda item: len(item[0]),
        reverse=True,
    )
)


def normalize_rule_id(rule_id: str) -> str:
    """Map legacy rule ids and prefixes to concept names; leave unknown ids unchanged."""
    exact = RULE_ID_ALIASES.get(rule_id)
    if exact is not None:
        return exact
    for old, new in _PREFIX_ALIASES:
        if rule_id == old or rule_id.startswith(old + "."):
            return new + rule_id[len(old) :]
    return rule_id


def rule_id_matches(actual: str, expected: str) -> bool:
    """True when ``actual`` and ``expected`` denote the same rule after normalization."""
    return normalize_rule_id(actual) == normalize_rule_id(expected)


__all__ = ["RULE_ID_ALIASES", "normalize_rule_id", "rule_id_matches"]
