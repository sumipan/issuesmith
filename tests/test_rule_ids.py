"""Rule id prefix normalization (#6125)."""

from __future__ import annotations

import pytest

from issuesmith.rule_ids import normalize_rule_id, rule_id_matches


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("cp1.intentional_hold", "design_lint.intentional_hold"),
        ("cp1.", "design_lint."),
        ("b1_ac_format", "ac_format"),
        ("scope_breadth.too_large", "scope_breadth.too_large"),
    ],
)
def test_normalize_rule_id_maps_legacy_prefixes(before: str, after: str) -> None:
    assert normalize_rule_id(before) == after


def test_rule_id_matches_accepts_legacy_or_concept_name() -> None:
    assert rule_id_matches("cp1.intentional_hold", "design_lint.intentional_hold")
