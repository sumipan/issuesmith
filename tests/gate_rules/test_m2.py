"""test_m2.py — M2Rules unit tests"""

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.m2 import M2Rules

# Japanese text intentionally kept for CJK processing test
BODY_WITH_UNCHECKED = """\
## 受け入れ条件

- [x] 完了した項目
- [ ] 未完了の項目
"""

# Japanese text intentionally kept for CJK processing test
BODY_ALL_CHECKED = """\
## 受け入れ条件

- [x] 完了した項目
- [x] これも完了
"""

# Japanese text intentionally kept for CJK processing test
BODY_NO_AC_SECTION = """\
## 背景

無関係なコンテンツ。

- [ ] これはセクション外のチェックボックス
"""

# Japanese text intentionally kept for CJK processing test
BODY_AC_SECTION_NO_CHECKBOXES = """\
## 受け入れ条件

受け入れ条件を自由記述で記載する。チェックボックスなし。
"""

# Japanese text intentionally kept for CJK processing test
BODY_MULTIPLE_UNCHECKED = """\
## 受け入れ条件

- [ ] 未完了 1
- [ ] 未完了 2
- [ ] 未完了 3
"""


def test_unchecked_returns_fail_violation():
    """AC: unchecked checkbox yields m2.unchecked_ac severity=fail."""
    violations = M2Rules().check(BODY_WITH_UNCHECKED, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.unchecked_ac"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "1" in v.message


def test_all_checked_returns_empty():
    """AC: all checkboxes checked → empty list."""
    violations = M2Rules().check(BODY_ALL_CHECKED, [])
    assert violations == []


def test_no_ac_section_returns_warn_violation():
    """AC: missing acceptance-criteria section → m2.ac_section_missing severity=warn."""
    violations = M2Rules().check(BODY_NO_AC_SECTION, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.ac_section_missing"
    assert v.severity == "warn"
    assert v.auto_fixable is False


def test_empty_body_returns_warn_violation():
    """AC: empty body → m2.ac_section_missing severity=warn (edge case)."""
    violations = M2Rules().check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.ac_section_missing"
    assert v.severity == "warn"


def test_ac_section_no_checkboxes_returns_empty():
    """AC: section present with no checkboxes → empty list."""
    violations = M2Rules().check(BODY_AC_SECTION_NO_CHECKBOXES, [])
    assert violations == []


def test_unchecked_count_in_message():
    """Unchecked count is included in the message."""
    violations = M2Rules().check(BODY_MULTIPLE_UNCHECKED, [])
    v = next(v for v in violations if v.rule_id == "m2.unchecked_ac")
    assert "3" in v.message


def test_labels_param_not_used():
    """labels is accepted for GateRule protocol compliance but does not affect behavior."""
    violations_no_label = M2Rules().check(BODY_WITH_UNCHECKED, [])
    violations_with_migration = M2Rules().check(BODY_WITH_UNCHECKED, ["scope:migration"])
    assert len(violations_no_label) == len(violations_with_migration)
    assert violations_no_label[0].rule_id == violations_with_migration[0].rule_id


def test_gate_registry_registered():
    import issuesmith.gate_rules.m2  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("m2") is M2Rules
