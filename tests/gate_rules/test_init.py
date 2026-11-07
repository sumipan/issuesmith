from issuesmith.gate_rules import GATE_REGISTRY, Violation


def test_violation_instantiation():
    v = Violation(
        rule_id="test.rule",
        severity="fail",
        message="msg",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )
    assert v.rule_id == "test.rule"
    assert v.severity == "fail"
    assert v.message == "msg"
    assert v.location is None
    assert v.auto_fixable is False
    assert v.fix_hint is None


def test_violation_with_all_fields():
    v = Violation(
        rule_id="design_lint.forbidden_word.tbd",
        severity="warn",
        message="TBD still present",
        location="line 5",
        auto_fixable=True,
        fix_hint="Replace with a concrete policy",
    )
    assert v.rule_id == "design_lint.forbidden_word.tbd"
    assert v.severity == "warn"
    assert v.location == "line 5"
    assert v.auto_fixable is True
    assert v.fix_hint == "Replace with a concrete policy"


def test_gate_registry_is_dict():
    assert isinstance(GATE_REGISTRY, dict)


def test_gate_registry_has_design_lint():
    assert "design_lint" in GATE_REGISTRY


def test_registry_has_only_concept_ids():
    import issuesmith.gates  # noqa: F401

    old_ids = (
        "cp1",
        "m2",
        "b1_ac_format",
        "b1_migration",
        "b1_milestone_subdesign",
    )
    new_ids = (
        "design_lint",
        "ac_complete",
        "ac_format",
        "migration_plan",
        "milestone_subdesign",
    )
    for gate_id in old_ids:
        assert gate_id not in GATE_REGISTRY
    for gate_id in new_ids:
        assert gate_id in GATE_REGISTRY


def test_gate_rule_protocol_compliance():
    class DummyRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    rule = DummyRule()
    result = rule.check("body text", ["label1"])
    assert result == []


def test_legacy_rule_classes_are_aliases_of_concept_rules():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        B1MilestoneSubdesignRules,
        _sub_plan_dependencies,
        parse_table_rows,
    )
    from issuesmith.gate_rules.ac_complete_rules import AcCompleteRules, get_unchecked_count
    from issuesmith.gate_rules.cp1 import Cp1Rules
    from issuesmith.gate_rules.design_lint_rules import DesignLintRules
    from issuesmith.gate_rules.m2 import M2Rules

    assert Cp1Rules is DesignLintRules
    assert M2Rules is AcCompleteRules
    assert callable(get_unchecked_count)
    assert callable(parse_table_rows)
    assert callable(_sub_plan_dependencies)
    assert B1MilestoneSubdesignRules is not None


def test_src_gate_rules_use_concept_rule_id_prefixes():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "issuesmith" / "gate_rules"
    pattern = re.compile(r'rule_id="(cp1|m2|b1_ac_format|b1_migration|b1_milestone_subdesign)\.')
    hits = []
    for path in root.rglob("*.py"):
        if path.name in {
            "cp1.py",
            "m2.py",
            "b1_ac_format.py",
            "b1_migration.py",
            "b1_milestone_subdesign.py",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        hits.extend(f"{path.name}:{m.start()}" for m in pattern.finditer(text))
    assert hits == []
