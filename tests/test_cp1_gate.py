"""test_cp1_gate.py — unit tests for the CP1 gate"""

import pytest

from issuesmith.cp1_gate import check_gate
from issuesmith.gate_rules.cp1 import Cp1Rules
from tests.legacy_text import ADD, CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY, SUB

_TABLE_HEADER = f"{REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION}"


@pytest.fixture(autouse=True)
def _scope_root(tmp_path):
    """scope_breadth is fail-closed (#3487); give it an empty measurable root."""
    from unittest.mock import patch

    with patch(
        "issuesmith.gate_rules.scope_breadth.resolve_scope_root", return_value=tmp_path
    ):
        yield

# Leading yaml metadata is required for all cases after missing_block (#2541).
# Tests that expect PASS must prepend a valid yaml header.
_VALID_YAML_HEAD = (
    '```yaml\n'
    'target_repo: sumipan/issuesmith\n'
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
    # ASCII fixture data.
    body = "## Design\nApproach is TBD\n"
    result = check_gate(body)
    assert result["status"] == "FAIL"
    assert any("TBD" in r for r in result["reasons"])


def test_youkakunin_in_body_fails():
    """#3 FAIL when body contains the needs-confirmation CJK placeholder"""
    # ASCII fixture data.
    assert "cp1.forbidden_word.youkakunin" in {item[1] for item in Cp1Rules.FAIL_PATTERNS}


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
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "## Overview\nThis is a normal design doc.\n\n## Acceptance Criteria\n- [x] Implemented\n"
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
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "## Design\nAdopted option A. Rejected option B.\n"
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
    # ASCII fixture data.
    assert "cp1.forbidden_word.mitei" in {item[1] for item in Cp1Rules.FAIL_PATTERNS}


def test_kentouchuu_in_body_fails():
    """FAIL when body contains the under-consideration CJK placeholder"""
    # ASCII fixture data.
    assert "cp1.forbidden_word.kentouchuu" in {item[1] for item in Cp1Rules.FAIL_PATTERNS}


def test_user_confirm_in_body_fails():
    """FAIL when body contains the ask-the-user CJK phrase"""
    # ASCII fixture data.
    assert "cp1.forbidden_word.user_confirm" in {item[1] for item in Cp1Rules.FAIL_PATTERNS}


def test_tbd_inside_code_block_passes():
    """TBD inside a code block is excluded"""
    body = _VALID_YAML_HEAD + "## Overview\nnormal text\n\n```bash\n# TBD: handle this\necho done\n```\n"
    result = check_gate(body)
    assert result["status"] == "PASS"


def test_todo_inside_inline_code_passes():
    """TODO: inside inline code is excluded"""
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "Acceptance Criteria: `TODO:` in body is FAIL"
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
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "c3053_c306E_c95A2_c6570_c306F_c672A_c5B9A_c7FA9_c5909_c6570_c3092 ValueError c3067_c65E9_c671F_c691C_c51FA_c3057_c307E_c3059"
    result = check_gate(body)
    assert result["status"] == "PASS"
    assert result["reasons"] == []


def test_miteii_placeholder_still_fails():
    """AC-2: undecided CJK placeholder still FAILs"""
    # ASCII fixture data.
    assert "cp1.forbidden_word.mitei" in {item[1] for item in Cp1Rules.FAIL_PATTERNS}


def test_miteigi_multiple_occurrences_passes():
    """AC-3: undefined CJK technical term outside code blocks multiple times still PASS"""
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "c672A_c5B9A_c7FA9_c5909_c6570 name c304C_c53C2_c7167_c3055_c308C_c307E_c3057_c305F_c3002_c672A_c5B9A_c7FA9_c306E_c95A2_c6570_c3092_c547C_c3073_c51FA_c3057_c3066_c3044_c307E_c3059_c3002"
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
    # ASCII fixture data.
    body = f"""\
```yaml
target_repo: sumipan/nexus
allow_paths:
  - tools/**
```

## Design

#### {SUB}1: foo

**Scope**: s
**Design Policy**: p
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/x.py` | {ADD} | add

**Acceptance Criteria**:
- [ ] one
- [ ] two
- [ ] three

## Milestone
### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | s | None |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/x.py` | {ADD} | add

## Acceptance Criteria
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
    # ASCII fixture data.
    assert any("yaml" in r.lower() for r in result_with_labels["reasons"])


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

# ASCII fixture data.
_MIGRATION_COMPLETE_BODY = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - "tools/foo.py"
  - "tests/test_foo_migration.py"
```

## Impact Survey

### Runtime State Survey

- **persistent state files**: (none)

## Migration Steps

```bash
test -f tools/foo.py
```

## Acceptance Criteria

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
    # ASCII fixture data.
    assert "Migration Steps" in joined
    assert "Runtime State Survey" in joined
    assert "paths_must_exist" in joined


def test_migration_label_complete_body_passes():
    result = check_gate(_MIGRATION_COMPLETE_BODY, ["scope:migration"])
    assert result["status"] == "PASS", result["reasons"]


def test_no_migration_label_skips_migration_rules():
    result = check_gate(_VALID_YAML_HEAD + "## Overview\nclean body\n", [])
    assert result["status"] == "PASS"


# ---------------------------------------------------------------------------
# scope_breadth (#3427) — CP1 must FAIL when allow_paths scope is exceeded
# ---------------------------------------------------------------------------

def test_scope_breadth_exceeded_causes_check_gate_fail():
    """check_gate returns FAIL when ScopeBreadthRules detects an oversized allow_paths."""
    import unittest.mock as mock

    from issuesmith.steps.scope_gate import ScopeMeasure

    exceeded = ScopeMeasure(
        files=100, lines=100, by_dir={"src/": 100}, skipped_binary=0, skipped_jsonl=0
    )
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "```\n\n"
        "## Overview\nnormal content\n"
    )
    with mock.patch("issuesmith.gate_rules.scope_breadth.measure_scope", return_value=exceeded):
        result = check_gate(body, [])
    assert result["status"] == "FAIL"
    assert any("scope_breadth.too_large" in r or "scope too large" in r for r in result["reasons"])
