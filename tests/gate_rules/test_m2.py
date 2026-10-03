"""test_m2.py — M2Rules unit tests"""

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.m2 import M2Rules

BODY_WITH_UNCHECKED = """\
## Acceptance Criteria

- [x] completed item
- [ ] unfinished item
"""

BODY_ALL_CHECKED = """\
## Acceptance Criteria

- [x] completed item
- [x] this is done too
"""

BODY_NO_AC_SECTION = """\
## Background

Unrelated content.

- [ ] this checkbox is outside the section
"""

BODY_AC_SECTION_NO_CHECKBOXES = """\
## Acceptance Criteria

The criteria are written as free text. No checkboxes.
"""

BODY_MULTIPLE_UNCHECKED = """\
## Acceptance Criteria

- [ ] unfinished 1
- [ ] unfinished 2
- [ ] unfinished 3
"""


def test_unchecked_returns_fail_violation():
    """AC: unchecked checkbox yields m2.unchecked_ac severity=fail."""
    violations = M2Rules().check(BODY_WITH_UNCHECKED, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.unchecked_ac"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "1" in v.message


def test_all_checked_returns_empty():
    """AC: all checkboxes checked → empty list."""
    violations = M2Rules().check(BODY_ALL_CHECKED, [])
    assert violations == []


def test_no_ac_section_returns_warn_violation():
    """AC: missing acceptance-criteria section → m2.ac_section_missing severity=warn."""
    violations = M2Rules().check(BODY_NO_AC_SECTION, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.ac_section_missing"
    assert v.severity == "warn"
    assert v.auto_fixable is False


def test_empty_body_returns_warn_violation():
    """AC: empty body → m2.ac_section_missing severity=warn (edge case)."""
    violations = M2Rules().check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "m2.ac_section_missing"
    assert v.severity == "warn"


def test_ac_section_no_checkboxes_returns_empty():
    """AC: section present with no checkboxes → empty list."""
    violations = M2Rules().check(BODY_AC_SECTION_NO_CHECKBOXES, [])
    assert violations == []


def test_unchecked_count_in_message():
    """Unchecked count is included in the message."""
    violations = M2Rules().check(BODY_MULTIPLE_UNCHECKED, [])
    v = next(v for v in violations if v.rule_id == "m2.unchecked_ac")
    assert "3" in v.message


def test_labels_param_not_used():
    """labels is accepted for GateRule protocol compliance but does not affect behavior."""
    violations_no_label = M2Rules().check(BODY_WITH_UNCHECKED, [])
    violations_with_migration = M2Rules().check(BODY_WITH_UNCHECKED, ["scope:migration"])
    assert len(violations_no_label) == len(violations_with_migration)
    assert violations_no_label[0].rule_id == violations_with_migration[0].rule_id


def test_gate_registry_registered():
    import issuesmith.gate_rules.m2  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("m2") is M2Rules


def test_messages_name_the_configured_section():
    from issuesmith.config import get_config

    heading = get_config().sections["acceptance_criteria"]
    [missing] = M2Rules().check(BODY_NO_AC_SECTION, [])
    assert missing.message == f"## {heading} section not found"
    [unchecked] = M2Rules().check(BODY_MULTIPLE_UNCHECKED, [])
    assert unchecked.message == f"3 unchecked item(s) remain in ## {heading}"
