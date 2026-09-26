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


def test_complete_migration_body_satisfies_ac_format_and_migration_gates():
    """One migration body must be able to satisfy every B1 gate at once (nexus #3899)."""
    from tests.gate_rules.test_b1_migration import BODY_COMPLETE

    rule_ids = {v.rule_id for v in collect_violations(_VALID_BODY + "\n" + BODY_COMPLETE, ["scope:migration"])}
    assert not {r for r in rule_ids if r.startswith(("b1_ac_format.", "b1_migration."))}, rule_ids


def test_format_report_shape():
    violations = collect_violations("## Overview\nno yaml\n", [])
    report = format_report(violations)
    assert report.startswith("VERIFY_FAILED_CHECKS: ")
    assert "## cp1.yaml_contract.missing_block" in report
    assert "FIX_HINT:" in report


def test_format_report_empty():
    assert format_report([]) == "VERIFY_FAILED_CHECKS: (none)\n"


def test_scope_size_violations_are_collected(tmp_path, monkeypatch):
    """scope_size runs in B1 Verify: the #3627 original fixture is reported (nexus #3665)."""
    import re
    from pathlib import Path

    import yaml

    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    raw = (
        Path(__file__).resolve().parent / "fixtures" / "issue_3627_original.md"
    ).read_text(encoding="utf-8")
    body = re.sub(r"c([0-9A-F]{4})_?", lambda m: chr(int(m.group(1), 16)), raw)
    try:
        violations = collect_violations(body, [])
    finally:
        reset_config_cache()
    assert any(v.rule_id.startswith("scope_size.") for v in violations)


_PROMOTED_MILESTONE_BODY = _VALID_BODY + (
    "\n## Design\nsingle-issue design written before the milestone promotion\n\n"
    "## Milestone\n\n"
    "### Sub-issue Plan\n"
    "| # | Title | Content | Dependency |\n"
    "|---|---|---|---|\n"
    "| 1 | a | scope a | None |\n"
    "| 2 | b | scope b | 1 |\n"
    "| 3 | c | scope c | 1 |\n"
)


def test_milestone_subdesign_gate_is_registered():
    from issuesmith.b1_verify import _GATES

    assert "b1_milestone_subdesign" in _GATES


def test_promoted_milestone_without_sub_blocks_fails_verify():
    """Regression (nexus #4002): 3 plan rows vs 0 sub blocks must not reach draft-done."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, ["scope:milestone"])}
    assert "b1_milestone_subdesign.sub_count_mismatch" in rule_ids


def test_non_milestone_verify_ignores_milestone_subdesign():
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, [])}
    assert not {r for r in rule_ids if r.startswith("b1_milestone_subdesign.")}, rule_ids


def test_complete_milestone_body_passes_milestone_subdesign():
    from tests.gate_rules.test_b1_milestone_subdesign import _valid_body

    rule_ids = {v.rule_id for v in collect_violations(_valid_body(), ["scope:milestone"])}
    assert not {r for r in rule_ids if r.startswith("b1_milestone_subdesign.")}, rule_ids
