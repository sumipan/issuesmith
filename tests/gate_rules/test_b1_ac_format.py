"""tests/gate_rules/test_b1_ac_format.py — b1_ac_format ゲートのユニットテスト。"""
from __future__ import annotations

import issuesmith.gate_rules.b1_ac_format  # noqa: F401 — registers on import
from issuesmith.gate_rules import GATE_REGISTRY


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_ac_format"]()
    return gate.check(body, labels)


NON_MILESTONE_LABELS = ["scope:feature", "issuesmith:develop-ready"]
MILESTONE_LABELS = ["scope:milestone"]

BODY_WITH_AC_AND_YAML = """\
## 背景・目的
some background

## 受け入れ条件

```yaml
paths_must_exist:
  - tools/foo/bar.py
paths_must_not_exist:
  - legacy/old_*
```

- [ ] 何かのチェック
"""

BODY_WITH_AC_NO_YAML = """\
## 受け入れ条件

- [ ] 何かのチェック
"""

BODY_NO_AC_SECTION = """\
## 背景・目的

some text

## 設計

some design
"""

BODY_WITH_INVALID_YAML = """\
## 受け入れ条件

```yaml
unknown_key:
  - foo.py
paths_must_exist:
  - bar.py
```
"""

BODY_WITH_UNPARSEABLE_YAML = """\
## 受け入れ条件

```yaml
: invalid: yaml: here
  - broken
```
"""

BODY_WITH_ALL_ALLOWED_KEYS = """\
## 受け入れ条件

```yaml
paths_must_exist:
  - a.py
paths_must_not_exist:
  - b.*
references_must_resolve:
  - file: config.yaml
    key_path: "jobs.*.script"
```
"""

BODY_WITH_DESIGN_TABLE = """\
## 設計

### 変更対象ファイル

| ファイルパス | 変更種別 | 変更内容 |
|---|---|---|
| `tools/foo/new_file.py` | 新規 | なにか |
| `tools/foo/existing.py` | 変更 | なにか |

## 受け入れ条件

```yaml
paths_must_exist:
  - tools/foo/new_file.py
```
"""


# ---------------------------------------------------------------------------
# scope:milestone ラベルなし → 常に空リスト
# ---------------------------------------------------------------------------


def test_no_milestone_label_returns_empty():
    violations = _check(BODY_NO_AC_SECTION, NON_MILESTONE_LABELS)
    assert violations == []


def test_no_milestone_label_with_valid_body_returns_empty():
    violations = _check(BODY_WITH_AC_AND_YAML, NON_MILESTONE_LABELS)
    assert violations == []


def test_empty_labels_returns_empty():
    violations = _check(BODY_NO_AC_SECTION, [])
    assert violations == []


# ---------------------------------------------------------------------------
# scope:milestone ラベルあり + ## 受け入れ条件 セクションなし → section_missing
# ---------------------------------------------------------------------------


def test_section_missing_violation():
    violations = _check(BODY_NO_AC_SECTION, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.section_missing"
    assert v.severity == "fail"
    assert v.auto_fixable is False


# ---------------------------------------------------------------------------
# scope:milestone ラベルあり + YAML ブロックなし → yaml_block_missing
# ---------------------------------------------------------------------------


def test_yaml_block_missing_violation():
    violations = _check(BODY_WITH_AC_NO_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.yaml_block_missing"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.fix_hint is not None


# ---------------------------------------------------------------------------
# scope:milestone ラベルあり + 不正 YAML → yaml_invalid
# ---------------------------------------------------------------------------


def test_unknown_key_in_yaml_returns_invalid():
    violations = _check(BODY_WITH_INVALID_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "b1_ac_format.yaml_invalid"
    assert violations[0].severity == "fail"
    assert violations[0].auto_fixable is False


def test_unparseable_yaml_returns_invalid():
    violations = _check(BODY_WITH_UNPARSEABLE_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "b1_ac_format.yaml_invalid"


# ---------------------------------------------------------------------------
# scope:milestone ラベルあり + 正常 YAML → 空リスト
# ---------------------------------------------------------------------------


def test_valid_yaml_returns_empty():
    violations = _check(BODY_WITH_AC_AND_YAML, MILESTONE_LABELS)
    assert violations == []


def test_all_allowed_keys_returns_empty():
    violations = _check(BODY_WITH_ALL_ALLOWED_KEYS, MILESTONE_LABELS)
    assert violations == []


def test_design_table_body_returns_empty():
    violations = _check(BODY_WITH_DESIGN_TABLE, MILESTONE_LABELS)
    assert violations == []


# ---------------------------------------------------------------------------
# GATE_REGISTRY への登録確認
# ---------------------------------------------------------------------------


def test_registered_in_gate_registry():
    assert "b1_ac_format" in GATE_REGISTRY


# ---------------------------------------------------------------------------
# auto_fix のヒント: yaml_block_missing 時に ## 設計 テーブルから派生
# ---------------------------------------------------------------------------


def test_yaml_block_missing_fix_hint_derives_from_design_table():
    body = """\
## 設計

### 変更対象ファイル

| ファイルパス | 変更種別 | 変更内容 |
|---|---|---|
| `tools/new_gate.py` | 新規 | 追加 |
| `tools/existing.py` | 変更 | 修正 |

## 受け入れ条件

- [ ] チェック
"""
    violations = _check(body, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.yaml_block_missing"
    assert "tools/new_gate.py" in (v.fix_hint or "")
    assert "tools/existing.py" not in (v.fix_hint or "")


# ---------------------------------------------------------------------------
# scope:migration も YAML 契約の形式検証対象
# ---------------------------------------------------------------------------


def test_migration_label_section_missing_violation():
    violations = _check(BODY_NO_AC_SECTION, ["scope:migration"])
    assert [v.rule_id for v in violations] == ["b1_ac_format.section_missing"]


def test_migration_label_with_valid_body_returns_empty():
    assert _check(BODY_WITH_AC_AND_YAML, ["scope:migration"]) == []
