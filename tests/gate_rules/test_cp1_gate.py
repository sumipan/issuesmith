"""test_cp1_gate.py — Cp1Rules unit tests using ASCII-only fixture data."""

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.cp1 import Cp1Rules

_VALID_YAML_HEAD = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "**"\n'
    "```\n\n"
)


def test_tbd_returns_violation():
    """AC: TBD yields a cp1.forbidden_word.tbd Violation."""
    violations = Cp1Rules().check("Body has TBD remaining", [])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location is None


def test_todo_returns_violation():
    violations = Cp1Rules().check("TODO: handle this later", [])
    assert any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.todo")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_clean_body_returns_empty():
    violations = Cp1Rules().check(_VALID_YAML_HEAD + "## Overview\nThis is a clean design doc.\n", [])
    assert violations == []


def test_code_block_excluded():
    body = "Normal text\n\n```python\n# TODO: remove\nFAIL_PATTERNS = []\n```\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)


def test_inline_code_excluded():
    body = _VALID_YAML_HEAD + "AC: body with `TODO:` is FAIL"
    violations = Cp1Rules().check(body, [])
    assert violations == []


def test_cp1_must_fail_true_returns_intentional_hold():
    body = "```yaml\ncp1_must_fail: true\n```\n\n## Overview\nNormal content\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.intentional_hold" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.intentional_hold")
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.fix_hint is None
    assert v.location is None


def test_cp1_must_fail_false_passes():
    body = "```yaml\ncp1_must_fail: false\n```\n\n## Overview\nContent\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_labels_param_ignored():
    violations = Cp1Rules().check("TBD", ["some-label", "other-label"])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)


def test_gate_registry_registered():
    import issuesmith.gate_rules.cp1  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("cp1") is Cp1Rules


def test_fix_hint_present_for_forbidden_words():
    violations = Cp1Rules().check("TBD", [])
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.fix_hint is not None


def test_yaml_missing_target_repo_is_auto_fixable():
    """Missing target_repo in YAML block → auto_fixable=True so B1 can fix."""
    body = "```yaml\nbase_branch: main\nallow_paths:\n  - src/**\n```\n\n## Overview\nContent\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.missing_required"), None)
    assert v is not None, "cp1.yaml_contract.missing_required should be detected"
    assert v.auto_fixable is True
    assert v.fix_hint is not None
    assert "target_repo" in v.fix_hint


def test_yaml_annotation_in_path_is_auto_fixable():
    """Parenthetical annotation in allow_paths → auto_fixable=True."""
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - (ghdag repo) src/**\n```\n"
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
    body = "```yaml\ntarget_repo: sumipan/unknown-repo\n```\n\n## Overview\nContent\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.unsupported_repo"), None)
    assert v is not None
    assert v.auto_fixable is False


def test_missing_yaml_block_is_fail_with_fix_hint():
    """Missing leading yaml is missing_block (no skip — #2539/#2541 regression)."""
    violations = Cp1Rules().check("## Overview\nno yaml block\n", [])
    by_id = {v.rule_id: v for v in violations}
    v = by_id["cp1.yaml_contract.missing_block"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "allow_paths" in (v.fix_hint or "")


def test_scope_gate_over_hard_max_returns_violation():
    """AC-3: scope_gate.max_files above hard_max_files fails CP1 yaml contract."""
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - tests/**\n"
        "scope_gate:\n"
        "  max_files: 250\n"
        "```\n\n## Overview\nNormal content\n"
    )
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max")
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "200" in (v.fix_hint or "") or "hard_max" in (v.fix_hint or "").lower()


def test_scope_gate_within_hard_max_is_ok():
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - tests/**\n"
        "scope_gate:\n"
        "  max_files: 150\n"
        "```\n\n## Overview\nNormal content\n"
    )
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max" for v in violations)


def test_broken_yaml_block_is_missing_block():
    body = "```yaml\n: : broken [\n```\n\n## Overview\nbody text\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)
