"""test_design_lint_gate.py — DesignLintRules unit tests using ASCII-only fixture data."""

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.design_lint_rules import DesignLintRules

_VALID_YAML_HEAD = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "**"\n'
    "```\n\n"
)


def test_todo_returns_violation():
    violations = DesignLintRules().check("TODO: handle this later", [])
    assert any(v.rule_id == "design_lint.forbidden_word.todo" for v in violations)
    v = next(v for v in violations if v.rule_id == "design_lint.forbidden_word.todo")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_clean_body_returns_empty():
    violations = DesignLintRules().check(_VALID_YAML_HEAD + "## Overview\nThis is a clean design doc.\n", [])
    assert violations == []


def test_code_block_excluded():
    body = "Normal text\n\n```python\n# TODO: remove\nFAIL_PATTERNS = []\n```\n"
    violations = DesignLintRules().check(body, [])
    assert not any(v.rule_id == "design_lint.forbidden_word.todo" for v in violations)


def test_inline_code_excluded():
    body = _VALID_YAML_HEAD + "AC: body with `TODO:` is FAIL"
    violations = DesignLintRules().check(body, [])
    assert violations == []


def test_cp1_must_fail_false_passes():
    body = "```yaml\ncp1_must_fail: false\n```\n\n## Overview\nContent\n"
    violations = DesignLintRules().check(body, [])
    assert not any(v.rule_id == "design_lint.intentional_hold" for v in violations)


def test_labels_param_ignored():
    violations = DesignLintRules().check("TBD", ["some-label", "other-label"])
    assert any(v.rule_id == "design_lint.forbidden_word.tbd" for v in violations)


def test_gate_registry_registered():
    import issuesmith.gate_rules.design_lint_rules  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("design_lint") is DesignLintRules


def test_fix_hint_present_for_forbidden_words():
    violations = DesignLintRules().check("TBD", [])
    v = next(v for v in violations if v.rule_id == "design_lint.forbidden_word.tbd")
    assert v.fix_hint is not None


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
    violations = DesignLintRules().check(body, [])
    assert any(v.rule_id == "design_lint.yaml_contract.scope_gate_over_hard_max" for v in violations)
    v = next(v for v in violations if v.rule_id == "design_lint.yaml_contract.scope_gate_over_hard_max")
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
    violations = DesignLintRules().check(body, [])
    assert not any(v.rule_id == "design_lint.yaml_contract.scope_gate_over_hard_max" for v in violations)


def test_broken_yaml_block_is_missing_block():
    body = "```yaml\n: : broken [\n```\n\n## Overview\nbody text\n"
    violations = DesignLintRules().check(body, [])
    assert any(v.rule_id == "design_lint.yaml_contract.missing_block" for v in violations)
