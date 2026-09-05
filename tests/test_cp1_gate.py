"""test_cp1_gate.py — CP1 ゲートのユニットテスト"""

from issuesmith.cp1_gate import check_gate

# 冒頭 yaml メタデータは missing_block 化（#2541）により全件必須。
# PASS を期待するテストは有効な yaml ヘッダを前置する。
_VALID_YAML_HEAD = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    '```\n\n'
)


def test_todo_in_body_fails():
    """#1 body に TODO: が含まれる場合は FAIL"""
    body = "## 概要\n後で決める TODO: 後で決める\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TODO:" in r for r in result["reasons"])


def test_tbd_in_body_fails():
    """#2 body に TBD が含まれる場合は FAIL"""
    body = "## 設計\n方針は TBD\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TBD" in r for r in result["reasons"])


def test_youkakunin_in_body_fails():
    """#3 body に 要確認 が含まれる場合は FAIL"""
    body = "## メモ\nこの部分は要確認\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("要確認" in r for r in result["reasons"])


def test_todo_inside_code_block_passes():
    """#4 コードブロック内の TODO: は除外される"""
    body = _VALID_YAML_HEAD + "## 概要\n通常テキスト\n\n```python\n# TODO: remove this\nFAIL_PATTERNS = []\n```\n"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_cp1_must_fail_true_fails():
    """#5 YAML frontmatter に cp1_must_fail: true がある場合は FAIL"""
    body = "```yaml\ncp1_must_fail: true\n```\n\n## 概要\n通常の内容\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("cp1_must_fail" in r for r in result["reasons"])
    assert result["intentional_hold"] is True


def test_clean_body_passes():
    """#6 FAIL パターンを含まない body は PASS"""
    body = _VALID_YAML_HEAD + "## 概要\nこれは普通の設計書です。\n\n## 受け入れ条件\n- [x] 実装済み\n"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []
    assert result["intentional_hold"] is False


def test_todo_fail_sets_intentional_hold_false():
    """TODO 起因の FAIL は intentional_hold false を返す"""
    body = "## 概要\nTODO: 詳細を追記\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is False


def test_multiple_patterns_all_listed():
    """#7 複数パターンが同時に存在する場合は全て列挙される"""
    body = "## 概要\nTODO: 後で決める\nTBD\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert len(result["reasons"]) >= 2
    assert any("TODO:" in r for r in result["reasons"])
    assert any("TBD" in r for r in result["reasons"])


def test_case_a_in_body_passes():
    """#8 「案A を採用した」はコードゲート対象外（PASS）"""
    body = _VALID_YAML_HEAD + "## 設計\n案A を採用した。案B は却下。\n"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_cp1_must_fail_false_passes():
    """cp1_must_fail: false はゲートを通過する（target_repo 必須化後も同様）"""
    body = "```yaml\ncp1_must_fail: false\ntarget_repo: sumipan/nexus\n```\n\n## 概要\n内容\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_miteii_in_body_fails():
    """「未定」が含まれる場合は FAIL"""
    body = "## 設計\n方針は未定です。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("未定" in r for r in result["reasons"])


def test_kentouchuu_in_body_fails():
    """「検討中」が含まれる場合は FAIL"""
    body = "## 設計\n実装方法は検討中です。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("検討中" in r for r in result["reasons"])


def test_user_confirm_in_body_fails():
    """「ユーザーに確認」が含まれる場合は FAIL"""
    body = "## 設計\nこの点はユーザーに確認してください。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("ユーザーに確認" in r for r in result["reasons"])


def test_tbd_inside_code_block_passes():
    """コードブロック内の TBD は除外される"""
    body = _VALID_YAML_HEAD + "## 概要\n通常テキスト\n\n```bash\n# TBD: handle this\necho done\n```\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_todo_inside_inline_code_passes():
    """インラインコード内の TODO: は除外される"""
    body = _VALID_YAML_HEAD + "受け入れ条件: `TODO:` を含む body は FAIL"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_todo_in_unclosed_inline_code_still_fails():
    """閉じバッククォートなしの TODO: は通常テキスト扱いで FAIL"""
    body = "これは `TODO: 閉じ忘れ\n次の行"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TODO:" in r for r in result["reasons"])


def test_cp1_must_fail_not_in_frontmatter_passes():
    """cp1_must_fail がコードブロック外に記載されても frontmatter 判定に影響しない"""
    body = _VALID_YAML_HEAD + "## 概要\nここでは cp1_must_fail の説明をしています。\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_miteigi_in_body_passes():
    """AC-1: 「未定義変数」を含む本文は PASS（技術用語の誤検出防止）"""
    body = _VALID_YAML_HEAD + "この関数は未定義変数を ValueError で早期検出します"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_miteii_placeholder_still_fails():
    """AC-2: プレースホルダー「未定」は引き続き FAIL"""
    body = "## 設計\n方針は未定です。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("未定" in r for r in result["reasons"])


def test_miteigi_multiple_occurrences_passes():
    """AC-3: 「未定義」がコードブロック外に複数回出現しても PASS"""
    body = _VALID_YAML_HEAD + "未定義変数 name が参照されました。未定義の関数を呼び出しています。"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


# --- Issue #1774: YAML ブロックに target_repo がない場合は CP1 FAIL ---

def test_yaml_block_without_target_repo_fails():
    """YAML ブロックに target_repo なし → FAIL with yaml_contract reason"""
    body = "```yaml\nbase_branch: main\nallow_paths:\n  - workflows/issuesmith/brushup.md\n```\n\n## 概要\n内容\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("target_repo" in r for r in result["reasons"])
    assert result["intentional_hold"] is False


def test_check_gate_with_milestone_labels_runs_milestone_checks():
    """labels 引数で scope:milestone を渡すと milestone チェックが実行される"""
    body = """\
```yaml
target_repo: sumipan/nexus
allow_paths:
  - tools/**
```

## 設計

#### サブ1: foo

**スコープ**: s
**設計方針**: p
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `tools/x.py` | 新規 | add

**受け入れ条件**:
- [ ] one
- [ ] two
- [ ] three

## マイルストーン
### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | foo | s | なし |

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `tools/x.py` | 新規 | add

## 受け入れ条件
```yaml
paths_must_exist:
  - tools/x.py
```
- [ ] meta close 本 Issue を close する
"""
    result_no_labels = check_gate(body)
    result_with_labels = check_gate(body, ["scope:milestone"])
    assert result_no_labels["status"] == "PASS"
    assert result_with_labels["status"] == "FAIL"
    assert any("yaml ブロック" in r for r in result_with_labels["reasons"])


def test_check_gate_labels_none_defaults_empty():
    """labels=None は空リストとして互換"""
    body = "## 概要\nクリーンな本文\n"
    assert check_gate(body) == check_gate(body, None)


# --- Issue #2419: scope:milestone ラベルで intentional_hold: True ---

def test_scope_milestone_label_sets_intentional_hold():
    """scope:milestone ラベルがあれば body の内容に関わらず intentional_hold: True"""
    body = "## 概要\nこれは milestone issue です。\n"
    result = check_gate(body, ["scope:milestone"])
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is True


def test_scope_milestone_clean_body_still_intentional_hold():
    """FAIL_PATTERNS が存在しないクリーンな body でも scope:milestone なら intentional_hold: True"""
    body = "## 概要\nクリーンな内容のみ。\n"
    result = check_gate(body, ["scope:milestone"])
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is True


def test_no_scope_milestone_label_intentional_hold_false():
    """scope:milestone なし（空ラベル）は intentional_hold: False"""
    body = "## 概要\nクリーンな本文\n"
    result = check_gate(body, [])
    assert result["intentional_hold"] is False


def test_other_label_does_not_set_intentional_hold():
    """scope:milestone 以外のラベルは intentional_hold に影響しない"""
    body = "## 概要\nクリーンな本文\n"
    result = check_gate(body, ["scope:migration"])
    assert result["intentional_hold"] is False


def test_scope_milestone_with_cp1_must_fail_still_intentional_hold():
    """cp1_must_fail: true + scope:milestone の組み合わせでも intentional_hold: True"""
    body = "```yaml\ncp1_must_fail: true\ntarget_repo: sumipan/nexus\n```\n\n## 概要\n内容\n"
    result = check_gate(body, ["scope:milestone"])
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is True


# ---------------------------------------------------------------------------
# scope:migration — migration 決定論ルールを CP1 でマージして強制
# ---------------------------------------------------------------------------

_MIGRATION_COMPLETE_BODY = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - "tools/foo.py"
  - "tests/test_foo_migration.py"
```

## 影響範囲調査

### 実行時状態の調査

- **永続 state ファイル**: （該当なし）

## マイグレーション手順

```bash
test -f tools/foo.py
```

## 受け入れ条件

```yaml
paths_must_exist:
  - tests/test_foo_migration.py
post_merge:
  - kind: stable_install
    repo: sumipan/issuesmith
    path: /var/tmp/issuesmith
removed_trees:
  - tools/issuesmith
```

- [ ] 移行テストが通ること
"""


def test_migration_label_merges_migration_violations():
    """migration 要件を欠く body は scope:migration 付きで CP1 FAIL になる"""
    result = check_gate("## 概要\nクリーンな本文\n", ["scope:migration"])
    assert result["status"] == "FAIL"
    joined = "\n".join(result["reasons"])
    assert "マイグレーション手順" in joined
    assert "実行時状態の調査" in joined
    assert "paths_must_exist" in joined


def test_migration_label_complete_body_passes():
    result = check_gate(_MIGRATION_COMPLETE_BODY, ["scope:migration"])
    assert result["status"] == "PASS", result["reasons"]


def test_no_migration_label_skips_migration_rules():
    result = check_gate(_VALID_YAML_HEAD + "## 概要\nクリーンな本文\n", [])
    assert result["status"] == "PASS"
