"""tests/gate_rules/test_b1_milestone_subdesign.py — b1_milestone_subdesign ゲートのユニットテスト。"""
from __future__ import annotations

import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: F401
from issuesmith.gate_rules import GATE_REGISTRY

MILESTONE_LABELS = ["scope:milestone"]
NON_MILESTONE_LABELS = ["scope:feature"]


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_milestone_subdesign"]()
    return gate.check(body, labels)


def _sub_block(
    num: int,
    title: str,
    path: str,
    *,
    repo: str = "sumipan/nexus",
    change_type: str = "新規",
    ac_items: list[str] | None = None,
) -> str:
    if ac_items is None:
        ac_items = [
            f"サブ{num} の受け入れ条件項目 alpha",
            f"サブ{num} の受け入れ条件項目 beta",
            f"サブ{num} の受け入れ条件項目 gamma",
        ]
    ac_lines = "\n".join(f"- [ ] {item}" for item in ac_items)
    return f"""\
#### サブ{num}: {title}

**スコープ**: サブ{num} の実装範囲を具体化
**設計方針**: サブ{num} の設計方針
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `{repo}` | `{path}` | {change_type} | 変更内容 |

**受け入れ条件**:
{ac_lines}
"""


def _valid_body(*, sub2_path: str = "tools/foo/b.py") -> str:
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## 設計

{_sub_block(1, "foo", "tools/foo/a.py")}
{_sub_block(2, "bar", sub2_path)}

## マイルストーン

### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | foo | scope1 | なし |
| 2 | bar | scope2 | 1 |

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | 新規 | add a |
| `sumipan/nexus` | `{sub2_path}` | 新規 | add b |

## 受け入れ条件

```yaml
paths_must_exist:
  - tools/foo/a.py
  - {sub2_path}
```
"""


# milestone/30 (#1827) 由来のドリフト fixture — 7 カテゴリを意図的に含む
MILESTONE30_DRIFT_BODY = f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - docs/MLTGNT-ROADMAP.md
```

## 設計

{_sub_block(1, "Phase A", "skills/_template/README.md")}
#### サブ2: Phase B

**スコープ**: SKILL.md 手順書化
**設計方針**: 命令形で記述
**変更対象ファイル**:
| ファイルパス | 変更種別 | 変更内容 |
|---|---|---|
| `skills/mltgnt-skill/SKILL.md` | 修正 | ガイドライン追加 |

**受け入れ条件**:
- [ ] 正しく動作すること
- [ ] ガイドラインが存在する

#### サブ3: Phase D

**スコープ**: runner 拡張
**設計方針**: SkillRunResult 追加
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/mltgnt` | `src/mltgnt/skill/runner.py` | 修正 | 戻り値変更 |

**受け入れ条件**:
- [ ] runner.run が SkillRunResult を返す
- [ ] diagnostics フィールドが存在する
- [ ] 既存テストが通る

## マイルストーン

### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | Phase A | README 規約 | なし |
| 2 | Phase B | 手順書化 | 1 |
| 3 | Phase D | runner 拡張 | 1 |

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `docs/MLTGNT-ROADMAP.md` | 修正 | 状態更新 |

## 影響範囲調査

| ファイル | 参照箇所 | 影響 |
|---------|---------|------|
| `mltgnt/src/mltgnt/skill/runner.py` | run() 戻り値 | 型変更 |

## 受け入れ条件

```yaml
paths_must_exist:
  - skills/_template/README.md
```
"""


def test_no_milestone_label_returns_empty():
    assert _check(_valid_body(), NON_MILESTONE_LABELS) == []


def test_valid_body_passes():
    assert _check(_valid_body(), MILESTONE_LABELS) == []


def test_sub_count_mismatch():
    body = _valid_body().replace("| 2 | bar | scope2 | 1 |", "")
    violations = _check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.sub_count_mismatch" for v in violations)


def test_subsection_missing():
    body = _valid_body().replace("**設計方針**: サブ1 の設計方針", "")
    violations = _check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.subsection_missing" for v in violations)


def test_table_schema_three_columns():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.table_schema" for v in violations)


def test_repo_mismatch_mltgnt():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.repo_mismatch" for v in violations)


def test_file_union_missing_in_parent():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(
        v.rule_id == "b1_milestone_subdesign.file_union_missing_in_parent"
        for v in violations
    )


def test_ac_vague_word():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.ac_vague_word" for v in violations)


def test_ac_count_too_few():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.ac_count" for v in violations)


def test_impact_scope_pollution():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.impact_scope_pollution" for v in violations)


def test_repo_mismatch_auto_fixable():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    repo_v = next(v for v in violations if v.rule_id == "b1_milestone_subdesign.repo_mismatch")
    assert repo_v.auto_fixable is True
    assert "target_repo:" in (repo_v.fix_hint or "")


def test_registered_in_gate_registry():
    assert "b1_milestone_subdesign" in GATE_REGISTRY


def test_milestone30_drift_detects_multiple_categories():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    rule_ids = {v.rule_id for v in violations}
    expected = {
        "b1_milestone_subdesign.table_schema",
        "b1_milestone_subdesign.repo_mismatch",
        "b1_milestone_subdesign.file_union_missing_in_parent",
        "b1_milestone_subdesign.ac_vague_word",
        "b1_milestone_subdesign.ac_count",
        "b1_milestone_subdesign.impact_scope_pollution",
    }
    assert expected.issubset(rule_ids)
