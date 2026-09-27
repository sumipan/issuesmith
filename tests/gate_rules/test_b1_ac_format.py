"""tests/gate_rules/test_b1_ac_format.py — unit tests for the b1_ac_format gate."""
from __future__ import annotations

import issuesmith.gate_rules.b1_ac_format  # noqa: F401 — registers on import
from issuesmith.gate_rules import GATE_REGISTRY


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_ac_format"]()
    return gate.check(body, labels)


NON_MILESTONE_LABELS = ["scope:feature", "issuesmith:develop-ready"]
MILESTONE_LABELS = ["scope:milestone"]

# ASCII fixture data.
BODY_WITH_AC_AND_YAML = """\
## Background
some background

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/bar.py
paths_must_not_exist:
  - legacy/old_*
```

- [ ] c4F55_c304B_c306E_c30C1_c30A7_c30C3_c30AF
"""

# ASCII fixture data.
BODY_WITH_AC_NO_YAML = """\
## Acceptance Criteria

- [ ] c4F55_c304B_c306E_c30C1_c30A7_c30C3_c30AF
"""

# ASCII fixture data.
BODY_NO_AC_SECTION = """\
## Background

some text

## Design

some design
"""

# ASCII fixture data.
BODY_WITH_INVALID_YAML = """\
## Acceptance Criteria

```yaml
unknown_key:
  - foo.py
paths_must_exist:
  - bar.py
```
"""

# ASCII fixture data.
BODY_WITH_UNPARSEABLE_YAML = """\
## Acceptance Criteria

```yaml
: invalid: yaml: here
  - broken
```
"""

# ASCII fixture data.
BODY_WITH_ALL_ALLOWED_KEYS = """\
## Acceptance Criteria

```yaml
paths_must_exist:
  - a.py
paths_must_not_exist:
  - b.*
references_must_resolve:
  - file: config.yaml
    key_path: "jobs.*.script"
```
"""

# ASCII fixture data.
BODY_WITH_DESIGN_TABLE = """\
## Design

### Changed Files

| File Path | Change Type | Description |
|---|---|---|
| `tools/foo/new_file.py` | Add | c306A_c306B_c304B |
| `tools/foo/existing.py` | Change | c306A_c306B_c304B |

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/new_file.py
```
"""


# ---------------------------------------------------------------------------
# Without scope:milestone → always empty list
# ---------------------------------------------------------------------------


def test_no_milestone_label_returns_empty():
    violations = _check(BODY_NO_AC_SECTION, NON_MILESTONE_LABELS)
    assert violations == []


def test_no_milestone_label_with_valid_body_returns_empty():
    violations = _check(BODY_WITH_AC_AND_YAML, NON_MILESTONE_LABELS)
    assert violations == []


def test_empty_labels_returns_empty():
    violations = _check(BODY_NO_AC_SECTION, [])
    assert violations == []


# ---------------------------------------------------------------------------
# With scope:milestone + no acceptance-criteria section → section_missing
# ---------------------------------------------------------------------------


def test_section_missing_violation():
    violations = _check(BODY_NO_AC_SECTION, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.section_missing"
    assert v.severity == "fail"
    assert v.auto_fixable is False


# ---------------------------------------------------------------------------
# With scope:milestone + no YAML block → yaml_block_missing
# ---------------------------------------------------------------------------


def test_yaml_block_missing_violation():
    violations = _check(BODY_WITH_AC_NO_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.yaml_block_missing"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.fix_hint is not None


# ---------------------------------------------------------------------------
# With scope:milestone + invalid YAML → yaml_invalid
# ---------------------------------------------------------------------------


def test_unknown_key_in_yaml_returns_invalid():
    violations = _check(BODY_WITH_INVALID_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "b1_ac_format.yaml_invalid"
    assert violations[0].severity == "fail"
    assert violations[0].auto_fixable is False


def test_unparseable_yaml_returns_invalid():
    violations = _check(BODY_WITH_UNPARSEABLE_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "b1_ac_format.yaml_invalid"


# ---------------------------------------------------------------------------
# With scope:milestone + valid YAML → empty list
# ---------------------------------------------------------------------------


def test_valid_yaml_returns_empty():
    violations = _check(BODY_WITH_AC_AND_YAML, MILESTONE_LABELS)
    assert violations == []


def test_all_allowed_keys_returns_empty():
    violations = _check(BODY_WITH_ALL_ALLOWED_KEYS, MILESTONE_LABELS)
    assert violations == []


BODY_WITH_MIGRATION_KEYS = """\
## Acceptance Criteria

```yaml
paths_must_exist:
  - a.py
post_merge:
  - kind: stable_install
    repo: sumipan/issuesmith
    path: /var/tmp/issuesmith
removed_trees:
  - tools/issuesmith
```
"""


def test_migration_contract_keys_are_allowed():
    """post_merge / removed_trees are required by b1_migration; b1_ac_format must accept them."""
    assert _check(BODY_WITH_MIGRATION_KEYS, ["scope:migration"]) == []
    assert _check(BODY_WITH_MIGRATION_KEYS, MILESTONE_LABELS) == []


def test_design_table_body_returns_empty():
    violations = _check(BODY_WITH_DESIGN_TABLE, MILESTONE_LABELS)
    assert violations == []


# ---------------------------------------------------------------------------
# Registration in GATE_REGISTRY
# ---------------------------------------------------------------------------


def test_registered_in_gate_registry():
    assert "b1_ac_format" in GATE_REGISTRY


# ---------------------------------------------------------------------------
# auto_fix hint: for yaml_block_missing, derive from design-section table
# ---------------------------------------------------------------------------


def test_yaml_block_missing_fix_hint_derives_from_design_table():
    # ASCII fixture data.
    body = """\
## Design

### Changed Files

| File Path | Change Type | Description |
|---|---|---|
| `tools/new_gate.py` | Add | c8FFD_c52A0 |
| `tools/existing.py` | Change | Modify |

## Acceptance Criteria

- [ ] c30C1_c30A7_c30C3_c30AF
"""
    violations = _check(body, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.yaml_block_missing"
    assert "paths_must_exist" in (v.fix_hint or "")


# ---------------------------------------------------------------------------
# scope:migration is also subject to YAML contract format checks
# ---------------------------------------------------------------------------


def test_migration_label_section_missing_violation():
    violations = _check(BODY_NO_AC_SECTION, ["scope:migration"])
    assert [v.rule_id for v in violations] == ["b1_ac_format.section_missing"]


def test_migration_label_with_valid_body_returns_empty():
    assert _check(BODY_WITH_AC_AND_YAML, ["scope:migration"]) == []


# ---------------------------------------------------------------------------
# references_must_resolve — symbol form (path::symbol)
# ---------------------------------------------------------------------------

_BODY_REFS_SYMBOL_STRING = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - "src/x.py::sym"
```
"""

_BODY_REFS_SYMBOL_DICT = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - file: "src/x.py::sym"
```
"""

_BODY_REFS_VALID = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - "src/x.py"
  - file: "config.yaml"
  - file: "configs/a.yml"
    key_path: "paths"
```
"""


def test_references_symbol_form_string_detected():
    violations = _check(_BODY_REFS_SYMBOL_STRING, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.reference_symbol_form"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location == "references_must_resolve[0]"
    assert v.fix_hint is not None
    assert "path::symbol" in (v.fix_hint or "")


def test_references_symbol_form_dict_detected():
    violations = _check(_BODY_REFS_SYMBOL_DICT, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.reference_symbol_form"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location == "references_must_resolve[0]"


def test_references_valid_entries_no_violation():
    violations = _check(_BODY_REFS_VALID, MILESTONE_LABELS)
    assert violations == []


# ---------------------------------------------------------------------------
# references_must_resolve — invalid entries
# ---------------------------------------------------------------------------

_BODY_REFS_MISSING_FILE_KEY = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - key_path: "paths"
```
"""

_BODY_REFS_EXTRA_KEY = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - file: "a.yaml"
    key_path: "x"
    extra: bad
```
"""

_BODY_REFS_FILE_WRONG_TYPE = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - file: 123
```
"""

_BODY_REFS_NOT_A_LIST = """\
## Acceptance Criteria

```yaml
references_must_resolve: "src/x.py"
```
"""

_BODY_REFS_MIXED = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - "src/good.py"
  - "src/bad.py::sym"
  - key_path: "x"
```
"""


def test_references_entry_missing_file_key():
    violations = _check(_BODY_REFS_MISSING_FILE_KEY, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.reference_entry_invalid"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.location == "references_must_resolve[0]"


def test_references_entry_extra_key_invalid():
    violations = _check(_BODY_REFS_EXTRA_KEY, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve[0]"


def test_references_entry_file_wrong_type():
    violations = _check(_BODY_REFS_FILE_WRONG_TYPE, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve[0]"


def test_references_not_a_list():
    violations = _check(_BODY_REFS_NOT_A_LIST, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "b1_ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve"


def test_references_mixed_valid_and_invalid_returns_each_violation():
    violations = _check(_BODY_REFS_MIXED, MILESTONE_LABELS)
    assert len(violations) == 2
    rule_ids = {v.rule_id for v in violations}
    assert rule_ids == {"b1_ac_format.reference_symbol_form", "b1_ac_format.reference_entry_invalid"}
    locations = {v.location for v in violations}
    assert "references_must_resolve[1]" in locations
    assert "references_must_resolve[2]" in locations


def test_references_invalid_with_no_milestone_label_returns_empty():
    violations = _check(_BODY_REFS_SYMBOL_STRING, NON_MILESTONE_LABELS)
    assert violations == []


def test_references_invalid_with_no_labels_returns_empty():
    violations = _check(_BODY_REFS_MISSING_FILE_KEY, [])
    assert violations == []
