"""test_m2.py — M2Rules unit tests"""

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.m2 import M2Rules

# ASCII fixture data.
BODY_WITH_UNCHECKED = """\
## Acceptance Criteria

- [x] c5B8C_c4E86_c3057_c305F_c9805_c76EE
- [ ] c672A_c5B8C_c4E86_c306E_c9805_c76EE
"""

# ASCII fixture data.
BODY_ALL_CHECKED = """\
## Acceptance Criteria

- [x] c5B8C_c4E86_c3057_c305F_c9805_c76EE
- [x] c3053_c308C_c3082_c5B8C_c4E86
"""

# ASCII fixture data.
BODY_NO_AC_SECTION = """\
## Background

c7121_c95A2_c4FC2_c306A_c30B3_c30F3_c30C6_c30F3_c30C4_c3002

- [ ] c3053_c308C_c306F_c30BB_c30AF_c30B7_c30E7_c30F3_c5916_c306E_c30C1_c30A7_c30C3_c30AF_c30DC_c30C3_c30AF_c30B9
"""

# ASCII fixture data.
BODY_AC_SECTION_NO_CHECKBOXES = """\
## Acceptance Criteria

Acceptance Criteria_c3092_c81EA_c7531_c8A18_c8FF0_c3067_c8A18_c8F09_c3059_c308B_c3002_c30C1_c30A7_c30C3_c30AF_c30DC_c30C3_c30AF_c30B9_None_c3002
"""

# ASCII fixture data.
BODY_MULTIPLE_UNCHECKED = """\
## Acceptance Criteria

- [ ] c672A_c5B8C_c4E86 1
- [ ] c672A_c5B8C_c4E86 2
- [ ] c672A_c5B8C_c4E86 3
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
