"""test_m2.py — M2Rules ユニットテスト"""

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.m2 import M2Rules

BODY_WITH_UNCHECKED = """\
## 受け入れ条件

- [x] 完了した項目
- [ ] 未完了の項目
"""

BODY_ALL_CHECKED = """\
## 受け入れ条件

- [x] 完了した項目
- [x] これも完了
"""

BODY_NO_AC_SECTION = """\
## 背景

無関係なコンテンツ。

- [ ] これはセクション外のチェックボックス
"""

BODY_AC_SECTION_NO_CHECKBOXES = """\
## 受け入れ条件

受け入れ条件を自由記述で記載する。チェックボックスなし。
"""

BODY_MULTIPLE_UNCHECKED = """\
## 受け入れ条件

- [ ] 未完了 1
- [ ] 未完了 2
- [ ] 未完了 3
"""


def test_unchecked_returns_fail_violation():
    """AC: 未チェック checkbox がある場合 m2.unchecked_ac severity=fail を返す"""
    violations = M2Rules().check(BODY_WITH_UNCHECKED, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.unchecked_ac"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "1" in v.message


def test_all_checked_returns_empty():
    """AC: 全 checkbox がチェック済みの場合は空リスト"""
    violations = M2Rules().check(BODY_ALL_CHECKED, [])
    assert violations == []


def test_no_ac_section_returns_warn_violation():
    """AC: 受け入れ条件セクションが存在しない場合 m2.ac_section_missing severity=warn を返す"""
    violations = M2Rules().check(BODY_NO_AC_SECTION, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.ac_section_missing"
    assert v.severity == "warn"
    assert v.auto_fixable is False


def test_empty_body_returns_warn_violation():
    """AC: 空 body は m2.ac_section_missing severity=warn を返す（エッジケース）"""
    violations = M2Rules().check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.ac_section_missing"
    assert v.severity == "warn"


def test_ac_section_no_checkboxes_returns_empty():
    """AC: セクションあり・checkbox なしの場合は空リスト"""
    violations = M2Rules().check(BODY_AC_SECTION_NO_CHECKBOXES, [])
    assert violations == []


def test_unchecked_count_in_message():
    """未チェック数がメッセージに含まれる"""
    violations = M2Rules().check(BODY_MULTIPLE_UNCHECKED, [])
    v = next(v for v in violations if v.rule_id == "m2.unchecked_ac")
    assert "3" in v.message


def test_labels_param_not_used():
    """labels は GateRule プロトコル準拠のために受け取るが動作に影響しない"""
    violations_no_label = M2Rules().check(BODY_WITH_UNCHECKED, [])
    violations_with_migration = M2Rules().check(BODY_WITH_UNCHECKED, ["scope:migration"])
    assert len(violations_no_label) == len(violations_with_migration)
    assert violations_no_label[0].rule_id == violations_with_migration[0].rule_id


def test_gate_registry_registered():
    import issuesmith.gate_rules.m2  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("m2") is M2Rules
