"""test_b1_verify.py — unit tests for B1 deterministic Verify (#2541)."""
from __future__ import annotations

from issuesmith.b1_verify import collect_violations, format_report

_VALID_BODY = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    'scope_gate:\n'
    '  enabled: false\n'
    '```\n\n'
    "## Overview\nclean body\n"
)


def test_valid_body_has_no_violations():
    assert collect_violations(_VALID_BODY, []) == []


def test_missing_yaml_is_reported():
    violations = collect_violations("## Overview\nno yaml\n", [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)


def test_intentional_hold_is_excluded():
    """scope:milestone intentional_hold is not a B1 artifact defect; exclude it"""
    violations = collect_violations(_VALID_BODY, ["scope:milestone"])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_migration_rules_are_included():
    violations = collect_violations(_VALID_BODY, ["scope:migration"])
    assert any(v.rule_id.startswith("b1_migration.") for v in violations)


def test_format_report_shape():
    violations = collect_violations("## Overview\nno yaml\n", [])
    report = format_report(violations)
    assert report.startswith("VERIFY_FAILED_CHECKS: ")
    assert "## cp1.yaml_contract.missing_block" in report
    assert "FIX_HINT:" in report


def test_format_report_empty():
    assert format_report([]) == "VERIFY_FAILED_CHECKS: (none)\n"
