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
        rule_id="cp1.forbidden_word.tbd",
        severity="warn",
        message="TBD が残存",
        location="line 5",
        auto_fixable=True,
        fix_hint="具体的な方針に置き換えてください",
    )
    assert v.rule_id == "cp1.forbidden_word.tbd"
    assert v.severity == "warn"
    assert v.location == "line 5"
    assert v.auto_fixable is True
    assert v.fix_hint == "具体的な方針に置き換えてください"


def test_gate_registry_is_dict():
    assert isinstance(GATE_REGISTRY, dict)


def test_gate_registry_has_cp1():
    assert "cp1" in GATE_REGISTRY


def test_gate_rule_protocol_compliance():
    class DummyRule:
        def check(self, body: str, labels: list[str]) -> list[Violation]:
            return []

    rule = DummyRule()
    result = rule.check("body text", ["label1"])
    assert result == []
