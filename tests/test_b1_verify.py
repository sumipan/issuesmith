"""test_b1_verify.py — B1 決定論 Verify（#2541）のユニットテスト。"""
from __future__ import annotations

from issuesmith.b1_verify import collect_violations, format_report

_VALID_BODY = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    '```\n\n'
    "## 概要\nクリーンな本文\n"
)


def test_valid_body_has_no_violations():
    assert collect_violations(_VALID_BODY, []) == []


def test_missing_yaml_is_reported():
    violations = collect_violations("## 概要\nyaml なし\n", [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)


def test_intentional_hold_is_excluded():
    """scope:milestone の intentional_hold は B1 成果物不備ではないため除外"""
    violations = collect_violations(_VALID_BODY, ["scope:milestone"])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_migration_rules_are_included():
    violations = collect_violations(_VALID_BODY, ["scope:migration"])
    assert any(v.rule_id.startswith("b1_migration.") for v in violations)


def test_format_report_shape():
    violations = collect_violations("## 概要\nyaml なし\n", [])
    report = format_report(violations)
    assert report.startswith("VERIFY_FAILED_CHECKS: ")
    assert "## cp1.yaml_contract.missing_block" in report
    assert "FIX_HINT:" in report


def test_format_report_empty():
    assert format_report([]) == "VERIFY_FAILED_CHECKS: (none)\n"
