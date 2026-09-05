"""test_cp1.py — Cp1Rules ユニットテスト"""


from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.cp1 import Cp1Rules


def test_tbd_returns_violation():
    """AC: TBD は cp1.forbidden_word.tbd の Violation を返す"""
    violations = Cp1Rules().check("本文に TBD が残っている", [])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location is None


def test_todo_returns_violation():
    violations = Cp1Rules().check("TODO: 後で対応", [])
    assert any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.todo")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_youkakunin_returns_violation():
    violations = Cp1Rules().check("この箇所は要確認です", [])
    assert any(v.rule_id == "cp1.forbidden_word.youkakunin" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.youkakunin")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_mitei_returns_violation():
    violations = Cp1Rules().check("方針は未定です", [])
    assert any(v.rule_id == "cp1.forbidden_word.mitei" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.mitei")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_kentouchuu_returns_violation():
    violations = Cp1Rules().check("実装方法は検討中です", [])
    assert any(v.rule_id == "cp1.forbidden_word.kentouchuu" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.kentouchuu")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_user_confirm_returns_violation():
    violations = Cp1Rules().check("この点はユーザーに確認してください", [])
    assert any(v.rule_id == "cp1.forbidden_word.user_confirm" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.user_confirm")
    assert v.severity == "fail"
    assert v.auto_fixable is True


_VALID_YAML_HEAD = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    '```\n\n'
)


def test_clean_body_returns_empty():
    violations = Cp1Rules().check(_VALID_YAML_HEAD + "## 概要\nこれは普通の設計書です。\n", [])
    assert violations == []


def test_code_block_excluded():
    body = "通常テキスト\n\n```python\n# TODO: remove\nFAIL_PATTERNS = []\n```\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)


def test_inline_code_excluded():
    body = _VALID_YAML_HEAD + "受け入れ条件: `TODO:` を含む body は FAIL"
    violations = Cp1Rules().check(body, [])
    assert violations == []


def test_cp1_must_fail_true_returns_intentional_hold():
    body = "```yaml\ncp1_must_fail: true\n```\n\n## 概要\n通常の内容\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.intentional_hold" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.intentional_hold")
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.fix_hint is None
    assert v.location is None


def test_cp1_must_fail_false_passes():
    body = "```yaml\ncp1_must_fail: false\n```\n\n## 概要\n内容\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_miteigi_not_flagged():
    """「未定義」は未定として誤検出しない"""
    violations = Cp1Rules().check("未定義変数を参照しています", [])
    assert not any(v.rule_id == "cp1.forbidden_word.mitei" for v in violations)


def test_labels_param_ignored():
    """labels パラメータを受け取っても動作に影響しない"""
    violations = Cp1Rules().check("TBD", ["some-label", "other-label"])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)


def test_gate_registry_registered():
    import issuesmith.gate_rules.cp1  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("cp1") is Cp1Rules


def test_fix_hint_present_for_forbidden_words():
    violations = Cp1Rules().check("TBD", [])
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.fix_hint is not None


# --- Issue #1774: yaml_contract violations は auto_fixable=True ---

def test_yaml_missing_target_repo_is_auto_fixable():
    """YAML ブロックに target_repo がない → auto_fixable=True で B1 が修正できる"""
    body = "```yaml\nbase_branch: main\nallow_paths:\n  - src/**\n```\n\n## 概要\n内容\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.missing_required"), None)
    assert v is not None, "cp1.yaml_contract.missing_required が検出されるべき"
    assert v.auto_fixable is True
    assert v.fix_hint is not None
    assert "target_repo" in v.fix_hint


def test_yaml_annotation_in_path_is_auto_fixable():
    """allow_paths に括弧付き注記 → auto_fixable=True"""
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - (ghdag リポ) src/**\n```\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.annotation_in_path"), None)
    assert v is not None
    assert v.auto_fixable is True
    assert v.fix_hint is not None


def test_yaml_invalid_path_format_is_auto_fixable():
    """allow_paths に /var/tmp/ → auto_fixable=True"""
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - /var/tmp/ghdag/\n```\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.invalid_path_format"), None)
    assert v is not None
    assert v.auto_fixable is True
    assert v.fix_hint is not None


def test_yaml_unsupported_repo_is_not_auto_fixable():
    """未対応リポジトリ → auto_fixable=False（人間による判断が必要）"""
    body = "```yaml\ntarget_repo: sumipan/unknown-repo\n```\n\n## 概要\n内容\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.unsupported_repo"), None)
    assert v is not None
    assert v.auto_fixable is False


def test_missing_yaml_block_is_fail_with_fix_hint():
    """冒頭 yaml 欠落は missing_block（スキップ禁止 — #2539/#2541 回帰）"""
    violations = Cp1Rules().check("## 概要\nyaml なし\n", [])
    by_id = {v.rule_id: v for v in violations}
    v = by_id["cp1.yaml_contract.missing_block"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "allow_paths" in (v.fix_hint or "")


def test_broken_yaml_block_is_missing_block():
    body = "```yaml\n: : broken [\n```\n\n## 概要\n本文\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)
