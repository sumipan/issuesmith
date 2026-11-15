"""test_ac_complete.py — AcCompleteRules unit tests"""
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.ac_complete_rules import AcCompleteRules

BODY_WITH_UNCHECKED = '## Acceptance Criteria\n\n- [x] completed item\n- [ ] unfinished item\n'
BODY_ALL_CHECKED = '## Acceptance Criteria\n\n- [x] completed item\n- [x] this is done too\n'
BODY_AC_SECTION_NO_CHECKBOXES = '## Acceptance Criteria\n\nThe criteria are written as free text. No checkboxes.\n'
BODY_MULTIPLE_UNCHECKED = '## Acceptance Criteria\n\n- [ ] unfinished 1\n- [ ] unfinished 2\n- [ ] unfinished 3\n'

def test_all_checked_returns_empty():
    """AC: all checkboxes checked → empty list."""
    violations = AcCompleteRules().check(BODY_ALL_CHECKED, [])
    assert violations == []

def test_ac_section_no_checkboxes_returns_empty():
    """AC: section present with no checkboxes → empty list."""
    violations = AcCompleteRules().check(BODY_AC_SECTION_NO_CHECKBOXES, [])
    assert violations == []

def test_unchecked_count_in_message():
    """Unchecked count is included in the message."""
    violations = AcCompleteRules().check(BODY_MULTIPLE_UNCHECKED, [])
    v = next((v for v in violations if v.rule_id == 'ac_complete.unchecked_ac'))
    assert '3' in v.message

def test_labels_param_not_used():
    """labels is accepted for GateRule protocol compliance but does not affect behavior."""
    violations_no_label = AcCompleteRules().check(BODY_WITH_UNCHECKED, [])
    violations_with_migration = AcCompleteRules().check(BODY_WITH_UNCHECKED, ['scope:migration'])
    assert len(violations_no_label) == len(violations_with_migration)
    assert violations_no_label[0].rule_id == violations_with_migration[0].rule_id

def test_gate_registry_registered():
    assert GATE_REGISTRY.get('ac_complete') is AcCompleteRules
