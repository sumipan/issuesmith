import dataclasses

import issuesmith.gate_rules  # noqa: F401
from issuesmith.gate_rules import GATE_REGISTRY, Violation


def test_violation_asdict_key_order():
    v = Violation(
        rule_id="r",
        severity="fail",
        message="m",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )
    assert list(dataclasses.asdict(v).keys()) == [
        "rule_id",
        "severity",
        "message",
        "location",
        "auto_fixable",
        "fix_hint",
    ]


def test_rule_gates_registered():
    for gate_id in (
        "ac_format",
        "milestone_subdesign",
        "milestone_consistency",
        "design_lint",
    ):
        assert gate_id in GATE_REGISTRY


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


def test_canonical_rule_classes_are_registered():
    from issuesmith.gate_rules import GATE_REGISTRY
    from issuesmith.gate_rules.ac_complete_rules import AcCompleteRules
    from issuesmith.gate_rules.ac_format_rules import AcFormatRules
    from issuesmith.gate_rules.design_lint_rules import DesignLintRules
    from issuesmith.gate_rules.migration_plan_rules import MigrationPlanRules
    from issuesmith.gate_rules.milestone_subdesign_rules import MilestoneSubdesignRules

    expected = {
        "design_lint": DesignLintRules,
        "ac_complete": AcCompleteRules,
        "ac_format": AcFormatRules,
        "migration_plan": MigrationPlanRules,
        "milestone_subdesign": MilestoneSubdesignRules,
    }
    for gate_id, cls in expected.items():
        assert GATE_REGISTRY.get(gate_id) is cls, gate_id


def test_src_gate_rules_use_concept_rule_id_prefixes():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "issuesmith"
    pattern = re.compile(r'rule_id="(cp1|m2|b1_ac_format|b1_migration|b1_milestone_subdesign)\.')
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        hits.extend(f"{path.relative_to(root)}:{m.start()}" for m in pattern.finditer(text))
    assert hits == []
