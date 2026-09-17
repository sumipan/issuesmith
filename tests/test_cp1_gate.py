"""test_cp1_gate.py — unit tests for the CP1 gate"""

from issuesmith.cp1_gate import check_gate

# Leading yaml metadata is required for all cases after missing_block (#2541).
# Tests that expect PASS must prepend a valid yaml header.
_VALID_YAML_HEAD = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    '```\n\n'
)


def test_todo_in_body_fails():
    """#1 FAIL when body contains TODO:"""
    body = "## Overview\ndecide later TODO: decide later\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TODO:" in r for r in result["reasons"])


def test_tbd_in_body_fails():
    """#2 FAIL when body contains TBD"""
    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\nApproach is TBD\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TBD" in r for r in result["reasons"])


def test_youkakunin_in_body_fails():
    """#3 FAIL when body contains the needs-confirmation CJK placeholder"""
    # Japanese text intentionally kept for CJK processing test
    body = "## Notes\nThis part is 要確認\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("要確認" in r for r in result["reasons"])


def test_todo_inside_code_block_passes():
    """#4 TODO: inside a code block is excluded"""
    body = _VALID_YAML_HEAD + "## Overview\nnormal text\n\n```python\n# TODO: remove this\nFAIL_PATTERNS = []\n```\n"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_cp1_must_fail_true_fails():
    """#5 FAIL when YAML frontmatter has cp1_must_fail: true"""
    body = "```yaml\ncp1_must_fail: true\n```\n\n## Overview\nnormal content\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("cp1_must_fail" in r for r in result["reasons"])
    assert result["intentional_hold"] is True


def test_clean_body_passes():
    """#6 PASS when body has no FAIL patterns"""
    # Japanese text intentionally kept for CJK processing test
    body = _VALID_YAML_HEAD + "## Overview\nThis is a normal design doc.\n\n## 受け入れ条件\n- [x] Implemented\n"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []
    assert result["intentional_hold"] is False


def test_todo_fail_sets_intentional_hold_false():
    """TODO-caused FAIL returns intentional_hold false"""
    body = "## Overview\nTODO: add details\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is False


def test_multiple_patterns_all_listed():
    """#7 When multiple patterns exist, all are listed"""
    body = "## Overview\nTODO: decide later\nTBD\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert len(result["reasons"]) >= 2
    assert any("TODO:" in r for r in result["reasons"])
    assert any("TBD" in r for r in result["reasons"])


def test_case_a_in_body_passes():
    """#8 'Adopted option A' is outside the code gate (PASS)"""
    # Japanese text intentionally kept for CJK processing test
    body = _VALID_YAML_HEAD + "## 設計\nAdopted option A. Rejected option B.\n"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_cp1_must_fail_false_passes():
    """cp1_must_fail: false passes the gate (still true after target_repo became required)"""
    body = "```yaml\ncp1_must_fail: false\ntarget_repo: sumipan/nexus\n```\n\n## Overview\ncontent\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_miteii_in_body_fails():
    """FAIL when body contains the undecided CJK placeholder"""
    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\n方針は未定です。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("未定" in r for r in result["reasons"])


def test_kentouchuu_in_body_fails():
    """FAIL when body contains the under-consideration CJK placeholder"""
    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\n実装方法は検討中です。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("検討中" in r for r in result["reasons"])


def test_user_confirm_in_body_fails():
    """FAIL when body contains the ask-the-user CJK phrase"""
    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\nこの点はユーザーに確認してください。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("ユーザーに確認" in r for r in result["reasons"])


def test_tbd_inside_code_block_passes():
    """TBD inside a code block is excluded"""
    body = _VALID_YAML_HEAD + "## Overview\nnormal text\n\n```bash\n# TBD: handle this\necho done\n```\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_todo_inside_inline_code_passes():
    """TODO: inside inline code is excluded"""
    # Japanese text intentionally kept for CJK processing test
    body = _VALID_YAML_HEAD + "受け入れ条件: `TODO:` in body is FAIL"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_todo_in_unclosed_inline_code_still_fails():
    """TODO: with unclosed backtick is treated as normal text → FAIL"""
    body = "This is `TODO: unclosed\nnext line"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TODO:" in r for r in result["reasons"])


def test_cp1_must_fail_not_in_frontmatter_passes():
    """cp1_must_fail mentioned outside a code block does not affect frontmatter detection"""
    body = _VALID_YAML_HEAD + "## Overview\nHere we explain cp1_must_fail.\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_miteigi_in_body_passes():
    """AC-1: body containing 'undefined variable' (CJK technical term) is PASS (avoid false positives)"""
    # Japanese text intentionally kept for CJK processing test
    body = _VALID_YAML_HEAD + "この関数は未定義変数を ValueError で早期検出します"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_miteii_placeholder_still_fails():
    """AC-2: undecided CJK placeholder still FAILs"""
    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\n方針は未定です。\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("未定" in r for r in result["reasons"])


def test_miteigi_multiple_occurrences_passes():
    """AC-3: undefined CJK technical term outside code blocks multiple times still PASS"""
    # Japanese text intentionally kept for CJK processing test
    body = _VALID_YAML_HEAD + "未定義変数 name が参照されました。未定義の関数を呼び出しています。"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


# --- Issue #1774: YAML block without target_repo → CP1 FAIL ---

def test_yaml_block_without_target_repo_fails():
    """YAML block without target_repo → FAIL with yaml_contract reason"""
    body = "```yaml\nbase_branch: main\nallow_paths:\n  - workflows/issuesmith/brushup.md\n```\n\n## Overview\ncontent\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("target_repo" in r for r in result["reasons"])
    assert result["intentional_hold"] is False


def test_check_gate_with_milestone_labels_runs_milestone_checks():
    """Split plan present + no label → FAIL on milestone_consistency.
    With scope:milestone, existing milestone AC yaml checks run (#3077)."""
    # Japanese text intentionally kept for CJK processing test
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
- [ ] meta close this Issue
"""
    result_no_labels = check_gate(body)
    result_with_labels = check_gate(body, ["scope:milestone"])
    assert result_no_labels["status"] == "FAIL"
    assert any("scope:milestone" in r for r in result_no_labels["reasons"])
    assert result_with_labels["status"] == "FAIL"
    # Japanese text intentionally kept for CJK processing test
    assert any("yaml ブロック" in r for r in result_with_labels["reasons"])


def test_check_gate_labels_none_defaults_empty():
    """labels=None is compatible with an empty list"""
    body = "## Overview\nclean body\n"
    assert check_gate(body) == check_gate(body, None)


# --- Issue #2419: scope:milestone label sets intentional_hold: True ---

def test_scope_milestone_label_sets_intentional_hold():
    """With scope:milestone label, intentional_hold is True regardless of body content"""
    body = "## Overview\nThis is a milestone issue.\n"
    result = check_gate(body, ["scope:milestone"])
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is True


def test_scope_milestone_clean_body_still_intentional_hold():
    """Even a clean body without FAIL_PATTERNS gets intentional_hold: True with scope:milestone"""
    body = "## Overview\nclean content only.\n"
    result = check_gate(body, ["scope:milestone"])
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is True


def test_no_scope_milestone_label_intentional_hold_false():
    """Without scope:milestone (empty labels), intentional_hold is False"""
    body = "## Overview\nclean body\n"
    result = check_gate(body, [])
    assert result["intentional_hold"] is False


def test_other_label_does_not_set_intentional_hold():
    """Labels other than scope:milestone do not affect intentional_hold"""
    body = "## Overview\nclean body\n"
    result = check_gate(body, ["scope:migration"])
    assert result["intentional_hold"] is False


def test_scope_milestone_with_cp1_must_fail_still_intentional_hold():
    """cp1_must_fail: true + scope:milestone still yields intentional_hold: True"""
    body = "```yaml\ncp1_must_fail: true\ntarget_repo: sumipan/nexus\n```\n\n## Overview\ncontent\n"
    result = check_gate(body, ["scope:milestone"])
    assert result["status"] == "FAIL"
    assert result["intentional_hold"] is True


# ---------------------------------------------------------------------------
# scope:migration — merge deterministic migration rules into CP1 and enforce
# ---------------------------------------------------------------------------

# Japanese text intentionally kept for CJK processing test
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

- **persistent state files**: (none)

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

- [ ] Migration tests pass
"""


def test_migration_label_merges_migration_violations():
    """Body missing migration requirements FAILs CP1 when scope:migration is present"""
    result = check_gate("## Overview\nclean body\n", ["scope:migration"])
    assert result["status"] == "FAIL"
    joined = "\n".join(result["reasons"])
    # Japanese text intentionally kept for CJK processing test
    assert "マイグレーション手順" in joined
    assert "実行時状態の調査" in joined
    assert "paths_must_exist" in joined


def test_migration_label_complete_body_passes():
    result = check_gate(_MIGRATION_COMPLETE_BODY, ["scope:migration"])
    assert result["status"] == "PASS", result["reasons"]


def test_no_migration_label_skips_migration_rules():
    result = check_gate(_VALID_YAML_HEAD + "## Overview\nclean body\n", [])
    assert result["status"] == "PASS"
