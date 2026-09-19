"""test_cp1.py — Cp1Rules unit tests"""


from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.cp1 import Cp1Rules


def test_tbd_returns_violation():
    """AC: TBD yields a cp1.forbidden_word.tbd Violation."""
    # ASCII fixture data.
    violations = Cp1Rules().check("c672C_c6587_c306B TBD c304C_c6B8B_c3063_c3066_c3044_c308B", [])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location is None


def test_todo_returns_violation():
    # ASCII fixture data.
    violations = Cp1Rules().check("TODO: c5F8C_c3067_c5BFE_c5FDC", [])
    assert any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.todo")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_youkakunin_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.youkakunin" in rule_ids


def test_mitei_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.mitei" in rule_ids


def test_kentouchuu_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.kentouchuu" in rule_ids


def test_user_confirm_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.user_confirm" in rule_ids


_VALID_YAML_HEAD = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    '```\n\n'
)


def test_clean_body_returns_empty():
    # ASCII fixture data.
    violations = Cp1Rules().check(_VALID_YAML_HEAD + "## c6982_c8981\nc3053_c308C_c306F_c666E_c901A_c306E_Design_c66F8_c3067_c3059_c3002\n", [])
    assert violations == []


def test_code_block_excluded():
    # ASCII fixture data.
    body = "c901A_c5E38_c30C6_c30AD_c30B9_c30C8\n\n```python\n# TODO: remove\nFAIL_PATTERNS = []\n```\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)


def test_inline_code_excluded():
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "Acceptance Criteria: `TODO:` c3092_c542B_c3080 body c306F FAIL"
    violations = Cp1Rules().check(body, [])
    assert violations == []


def test_cp1_must_fail_true_returns_intentional_hold():
    # ASCII fixture data.
    body = "```yaml\ncp1_must_fail: true\n```\n\n## c6982_c8981\nc901A_c5E38_c306E_c5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.intentional_hold" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.intentional_hold")
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.fix_hint is None
    assert v.location is None


def test_cp1_must_fail_false_passes():
    # ASCII fixture data.
    body = "```yaml\ncp1_must_fail: false\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_miteigi_not_flagged():
    """The Japanese word for 'undefined' must not be false-positive-flagged as 'undecided'."""
    # ASCII fixture data.
    violations = Cp1Rules().check("c672A_c5B9A_c7FA9_c5909_c6570_c3092_c53C2_c7167_c3057_c3066_c3044_c307E_c3059", [])
    assert not any(v.rule_id == "cp1.forbidden_word.mitei" for v in violations)


def test_labels_param_ignored():
    """labels parameter is accepted but does not affect behavior."""
    violations = Cp1Rules().check("TBD", ["some-label", "other-label"])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)


def test_gate_registry_registered():
    import issuesmith.gate_rules.cp1  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("cp1") is Cp1Rules


def test_fix_hint_present_for_forbidden_words():
    violations = Cp1Rules().check("TBD", [])
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.fix_hint is not None


# --- Issue #1774: yaml_contract violations are auto_fixable=True ---

def test_yaml_missing_target_repo_is_auto_fixable():
    """Missing target_repo in YAML block → auto_fixable=True so B1 can fix."""
    # ASCII fixture data.
    body = "```yaml\nbase_branch: main\nallow_paths:\n  - src/**\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.missing_required"), None)
    assert v is not None, "cp1.yaml_contract.missing_required should be detected"
    assert v.auto_fixable is True
    assert v.fix_hint is not None
    assert "target_repo" in v.fix_hint


def test_yaml_annotation_in_path_is_auto_fixable():
    """Parenthetical annotation in allow_paths → auto_fixable=True."""
    # ASCII fixture data.
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - (ghdag c30EA_c30DD) src/**\n```\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.annotation_in_path"), None)
    assert v is not None
    assert v.auto_fixable is True
    assert v.fix_hint is not None


def test_yaml_invalid_path_format_is_auto_fixable():
    """allow_paths containing /var/tmp/ → auto_fixable=True."""
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - /var/tmp/ghdag/\n```\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.invalid_path_format"), None)
    assert v is not None
    assert v.auto_fixable is True
    assert v.fix_hint is not None


def test_yaml_unsupported_repo_is_not_auto_fixable():
    """Unsupported repository → auto_fixable=False (needs human judgment)."""
    # ASCII fixture data.
    body = "```yaml\ntarget_repo: sumipan/unknown-repo\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.unsupported_repo"), None)
    assert v is not None
    assert v.auto_fixable is False


def test_missing_yaml_block_is_fail_with_fix_hint():
    """Missing leading yaml is missing_block (no skip — #2539/#2541 regression)."""
    # ASCII fixture data.
    violations = Cp1Rules().check("## c6982_c8981\nyaml None\n", [])
    by_id = {v.rule_id: v for v in violations}
    v = by_id["cp1.yaml_contract.missing_block"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "allow_paths" in (v.fix_hint or "")


def test_scope_gate_over_hard_max_returns_violation():
    """AC-3: scope_gate.max_files above hard_max_files fails CP1 yaml contract."""
    # ASCII fixture data.
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - tests/**\n"
        "scope_gate:\n"
        "  max_files: 250\n"
        "```\n\n## c6982_c8981\nc901A_c5E38_c306E_c5185_c5BB9\n"
    )
    violations = Cp1Rules().check(body, [])
    assert any(
        v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max" for v in violations
    )
    v = next(
        v for v in violations if v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max"
    )
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "200" in (v.fix_hint or "") or "hard_max" in (v.fix_hint or "").lower()


def test_scope_gate_within_hard_max_is_ok():
    # ASCII fixture data.
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - tests/**\n"
        "scope_gate:\n"
        "  max_files: 150\n"
        "```\n\n## c6982_c8981\nc901A_c5E38_c306E_c5185_c5BB9\n"
    )
    violations = Cp1Rules().check(body, [])
    assert not any(
        v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max" for v in violations
    )


def test_broken_yaml_block_is_missing_block():
    # ASCII fixture data.
    body = "```yaml\n: : broken [\n```\n\n## c6982_c8981\nc672C_c6587\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)
