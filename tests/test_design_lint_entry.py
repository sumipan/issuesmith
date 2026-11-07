"""Design-lint entry gate intentional_hold handling (#6125)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from issuesmith.design_lint_entry import check_gate


def _violation(rule_id: str) -> SimpleNamespace:
    return SimpleNamespace(rule_id=rule_id, message="hold")


@patch("issuesmith.design_lint_entry.Cp1Rules")
@patch("issuesmith.design_lint_entry.MilestoneConsistencyRules")
@patch("issuesmith.design_lint_entry.ScopeBreadthRules")
@patch("issuesmith.design_lint_entry.ScopeCouplingRules")
def test_intentional_hold_true_for_legacy_rule_id(
    coupling_cls,
    scope_cls,
    milestone_cls,
    cp1_cls,
) -> None:
    for cls in (cp1_cls, milestone_cls, scope_cls, coupling_cls):
        cls.return_value.check.return_value = []
    cp1_cls.return_value.check.return_value = [_violation("cp1.intentional_hold")]

    result = check_gate("body", [])
    assert result["intentional_hold"] is True


@patch("issuesmith.design_lint_entry.Cp1Rules")
@patch("issuesmith.design_lint_entry.MilestoneConsistencyRules")
@patch("issuesmith.design_lint_entry.ScopeBreadthRules")
@patch("issuesmith.design_lint_entry.ScopeCouplingRules")
def test_intentional_hold_true_for_concept_rule_id(
    coupling_cls,
    scope_cls,
    milestone_cls,
    cp1_cls,
) -> None:
    for cls in (cp1_cls, milestone_cls, scope_cls, coupling_cls):
        cls.return_value.check.return_value = []
    cp1_cls.return_value.check.return_value = [_violation("design_lint.intentional_hold")]

    result = check_gate("body", [])
    assert result["intentional_hold"] is True
