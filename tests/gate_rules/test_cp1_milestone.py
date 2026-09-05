"""tests/gate_rules/test_cp1_milestone.py — CP1 milestone チェック 8〜11 のユニットテスト。"""
from __future__ import annotations

from issuesmith.gate_rules.cp1 import Cp1Rules, _keyword_tokens

MILESTONE_LABELS = ["scope:milestone"]


def _sub_block(num: int, *, with_yaml: bool = True, extra_text: str = "") -> str:
    yaml_block = """\
```yaml
paths_must_exist:
  - tools/foo/a.py
```
""" if with_yaml else ""
    return f"""\
#### サブ{num}: foo

**スコープ**: scope
**設計方針**: plan
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | 新規 | add

**受け入れ条件**:
{yaml_block}- [ ] サブ{num} alpha 項目テスト
- [ ] サブ{num} beta 項目テスト
- [ ] サブ{num} gamma 項目テスト
{extra_text}
"""


def _milestone_body(*, parent_ac: list[str] | None = None, sub_extra: str = "") -> str:
    if parent_ac is None:
        parent_ac = [
            "tools/foo/a.py が存在する",
            "サブイシューすべての実装が完了する",
            "本 Issue を close する",
        ]
    parent_ac_lines = "\n".join(f"- [ ] {item}" for item in parent_ac)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## 設計

{_sub_block(1, extra_text=sub_extra)}

## マイルストーン

### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | foo | scope | なし |

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | 新規 | add

## 受け入れ条件

```yaml
paths_must_exist:
  - tools/foo/a.py
```

{parent_ac_lines}
"""


def test_check8_sub_block_todo():
    body = _milestone_body(sub_extra="TODO: 後で決める\n")
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.forbidden_word.todo.sub1" for v in violations)


def test_check8_code_block_todo_excluded():
    body = _milestone_body(sub_extra="```python\n# TODO: ignore\n```\n")
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any("forbidden_word.todo" in v.rule_id for v in violations)


def test_check9_sub_ac_yaml_missing():
    body = _milestone_body()
    body = body.replace(
        "```yaml\npaths_must_exist:\n  - tools/foo/a.py\n```\n",
        "",
        1,
    )
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.sub_ac_yaml_missing" for v in violations)


def test_check9_sub_ac_yaml_present_passes():
    violations = Cp1Rules().check(_milestone_body(), MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.sub_ac_yaml_missing" for v in violations)


def test_check10_parent_ac_orphan():
    body = _milestone_body(parent_ac=[
        "孤立した親受け入れ条件トークン xyzunique",
        "サブイシューすべての実装が完了する",
        "本 Issue を close する",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_meta_pattern_passes():
    body = _milestone_body(parent_ac=[
        "PR #9999 がマージ済み",
        "子 Issue 起票が完了",
        "本 Issue を close する",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_keyword_tokens_japanese_punctuation_split():
    tokens = _keyword_tokens("監査し、診断レポートを投稿")
    assert len(tokens) >= 2
    assert "監査し" in tokens
    assert "診断レポートを投稿" in tokens


def test_keyword_tokens_english_phase_unchanged():
    assert _keyword_tokens("Phase 4:") == ["Phase"]


def test_check10_japanese_punctuation_parent_ac_cover_passes():
    body = _milestone_body(
        parent_ac=[
            "監査し、診断レポートを投稿",
            "サブイシューすべての実装が完了する",
            "本 Issue を close する",
        ],
        sub_extra="- [ ] 診断レポートを投稿する\n",
    )
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_keyword_cover_passes():
    body = _milestone_body(parent_ac=[
        "サブ1 alpha 項目テストが pass する",
        "サブイシューすべての実装が完了する",
        "本 Issue を close する",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_optional_prefix_passes():
    body = _milestone_body(parent_ac=[
        "（オプション）ghdag 側で副作用ありスキルの動的直列化 PR が出ている",
        "サブイシューすべての実装が完了する",
        "本 Issue を close する",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_optional_prefix_midtext_fails():
    body = _milestone_body(parent_ac=[
        "ghdag 側で（オプション）副作用ありスキルの動的直列化 PR が出ている",
        "サブイシューすべての実装が完了する",
        "本 Issue を close する",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check11_paths_must_exist_unmapped():
    body = _milestone_body()
    body = body.replace(
        "## 受け入れ条件\n\n```yaml\npaths_must_exist:\n  - tools/foo/a.py\n```",
        "## 受け入れ条件\n\n```yaml\npaths_must_exist:\n  - tools/foo/a.py\n  - tools/missing/new.py\n```",
        1,
    )
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)


def test_check11_paths_must_exist_mapped_passes():
    violations = Cp1Rules().check(_milestone_body(), MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)


def _sub_block_modify(num: int, *, path: str = "src/ghdag/pipeline/audit_query.py") -> str:
    return f"""\
#### サブ{num}: audit

**スコープ**: scope
**設計方針**: plan
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/ghdag` | `{path}` | 修正 | add function

**受け入れ条件**:
```yaml
paths_must_exist:
  - {path}
```
- [ ] サブ{num} alpha 項目テスト
- [ ] サブ{num} beta 項目テスト
- [ ] サブ{num} gamma 項目テスト
"""


def _milestone_body_with_modify_path() -> str:
    return f"""\
```yaml
target_repo: sumipan/ghdag
base_branch: main
allow_paths:
  - src/ghdag/**
```

## 設計

{_sub_block_modify(1)}

## マイルストーン

### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | audit | scope | なし |

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/ghdag` | `src/ghdag/pipeline/audit_query.py` | 修正 | add function

## 受け入れ条件

```yaml
paths_must_exist:
  - src/ghdag/pipeline/audit_query.py
```

- [ ] サブイシューすべての実装が完了する
- [ ] 本 Issue を close する
"""


def test_check11_paths_must_exist_modify_mapped_passes():
    violations = Cp1Rules().check(_milestone_body_with_modify_path(), MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)


def test_milestone_checks_skipped_without_label():
    body = _milestone_body(sub_extra="TODO: 残存\n")
    violations = Cp1Rules().check(body, [])
    assert not any("milestone" in v.rule_id for v in violations)
    assert not any(".sub1" in v.rule_id for v in violations)


def _milestone_body_without_sub_blocks(*, parent_ac: list[str] | None = None) -> str:
    if parent_ac is None:
        parent_ac = [
            "孤立した親受け入れ条件トークン xyzunique",
            "サブイシューすべての実装が完了する",
            "本 Issue を close する",
        ]
    parent_ac_lines = "\n".join(f"- [ ] {item}" for item in parent_ac)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## 設計

## マイルストーン

### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | foo | scope | なし |

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | 新規 | add

## 受け入れ条件

```yaml
paths_must_exist:
  - tools/foo/a.py
  - tools/missing/new.py
```

{parent_ac_lines}
"""


def test_check10_parent_ac_orphan_skipped_without_sub_blocks():
    body = _milestone_body_without_sub_blocks()
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check11_paths_must_exist_unmapped_skipped_without_sub_blocks():
    body = _milestone_body_without_sub_blocks()
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)
