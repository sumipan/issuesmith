"""tests/gate_rules/test_ac_format.py — unit tests for the b1_ac_format gate."""
from __future__ import annotations

import subprocess
from pathlib import Path

import issuesmith.gate_rules.ac_format_rules  # noqa: F401 — registers on import
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.ac_format_rules import check_greps_against_base


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["ac_format"]()
    return gate.check(body, labels)


NON_MILESTONE_LABELS = ["scope:feature", "issuesmith:develop-ready"]
MILESTONE_LABELS = ["scope:milestone"]

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

- [ ] some check
"""

BODY_WITH_AC_NO_YAML = """\
## Acceptance Criteria

- [ ] some check
"""

BODY_NO_AC_SECTION = """\
## Background

some text

## Design

some design
"""

BODY_WITH_INVALID_YAML = """\
## Acceptance Criteria

```yaml
unknown_key:
  - foo.py
paths_must_exist:
  - bar.py
```
"""

BODY_WITH_UNPARSEABLE_YAML = """\
## Acceptance Criteria

```yaml
: invalid: yaml: here
  - broken
```
"""

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

BODY_WITH_DESIGN_TABLE = """\
## Design

### Changed Files

| File Path | Change Type | Description |
|---|---|---|
| `tools/foo/new_file.py` | Add | something |
| `tools/foo/existing.py` | Change | something |

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/new_file.py
```
"""


# ---------------------------------------------------------------------------
# Without scope:milestone / scope:migration — skip unless AC has YAML block
# ---------------------------------------------------------------------------


def test_no_milestone_label_returns_empty():
    violations = _check(BODY_NO_AC_SECTION, NON_MILESTONE_LABELS)
    assert violations == []


def test_no_milestone_label_with_valid_body_returns_empty():
    violations = _check(BODY_WITH_AC_AND_YAML, NON_MILESTONE_LABELS)
    assert violations == []


def test_no_milestone_label_with_ac_no_yaml_returns_empty():
    violations = _check(BODY_WITH_AC_NO_YAML, NON_MILESTONE_LABELS)
    assert violations == []


def test_empty_labels_returns_empty():
    violations = _check(BODY_NO_AC_SECTION, [])
    assert violations == []


# ---------------------------------------------------------------------------
# With scope:milestone + no acceptance-criteria section → section_missing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# With scope:milestone + no YAML block → yaml_block_missing
# ---------------------------------------------------------------------------


def test_yaml_block_missing_violation():
    violations = _check(BODY_WITH_AC_NO_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.yaml_block_missing"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.fix_hint is not None


# ---------------------------------------------------------------------------
# With scope:milestone + invalid YAML → yaml_invalid
# ---------------------------------------------------------------------------


def test_unknown_key_in_yaml_returns_invalid():
    violations = _check(BODY_WITH_INVALID_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.yaml_invalid"
    assert violations[0].severity == "fail"
    assert violations[0].auto_fixable is False


def test_unparseable_yaml_returns_invalid():
    violations = _check(BODY_WITH_UNPARSEABLE_YAML, MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.yaml_invalid"


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
    assert "ac_format" in GATE_REGISTRY


# ---------------------------------------------------------------------------
# auto_fix hint: for yaml_block_missing, derive from design-section table
# ---------------------------------------------------------------------------


def test_yaml_block_missing_fix_hint_derives_from_design_table():
    body = """\
## Design

### Changed Files

| File Path | Change Type | Description |
|---|---|---|
| `tools/new_gate.py` | Add | add |
| `tools/existing.py` | Change | Modify |

## Acceptance Criteria

- [ ] check
"""
    violations = _check(body, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.yaml_block_missing"
    assert v.fix_hint == "```yaml\npaths_must_exist:\n  - tools/new_gate.py\n```"


# ---------------------------------------------------------------------------
# scope:migration is also subject to YAML contract format checks
# ---------------------------------------------------------------------------


def test_migration_label_section_missing_violation():
    violations = _check(BODY_NO_AC_SECTION, ["scope:migration"])
    assert [v.rule_id for v in violations] == ["ac_format.section_missing"]


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


def test_references_symbol_form_dict_detected():
    violations = _check(_BODY_REFS_SYMBOL_DICT, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_symbol_form"
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

_BODY_REFS_KEY_PATH_WRONG_TYPE = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - file: "config.yaml"
    key_path: 123
```
"""

_BODY_REFS_NOT_A_LIST = """\
## Acceptance Criteria

```yaml
references_must_resolve: "src/x.py"
```
"""


def test_references_entry_extra_key_invalid():
    violations = _check(_BODY_REFS_EXTRA_KEY, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve[0]"


def test_references_entry_file_wrong_type():
    violations = _check(_BODY_REFS_FILE_WRONG_TYPE, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve[0]"


def test_references_entry_key_path_wrong_type():
    violations = _check(_BODY_REFS_KEY_PATH_WRONG_TYPE, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve[0]"


def test_references_not_a_list():
    violations = _check(_BODY_REFS_NOT_A_LIST, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_entry_invalid"
    assert v.location == "references_must_resolve"


def test_references_symbol_form_without_milestone_label():
    violations = _check(_BODY_REFS_SYMBOL_STRING, NON_MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.reference_symbol_form"


def test_references_symbol_form_without_labels():
    violations = _check(_BODY_REFS_SYMBOL_STRING, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.reference_symbol_form"


def test_references_invalid_without_milestone_label_when_yaml_present():
    violations = _check(_BODY_REFS_MISSING_FILE_KEY, NON_MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.reference_entry_invalid"


_BODY_REFS_ARROW = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - README.md -> TESTING.md
```
"""

_BODY_REFS_INVALID_DICT_EXTRA = """\
## Acceptance Criteria

```yaml
references_must_resolve:
  - file: x.yaml
    extra: 1
```
"""

def test_references_arrow_entry_is_auto_fixable_entry_invalid():
    violations = _check(_BODY_REFS_ARROW, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_entry_invalid"
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location == "references_must_resolve[0]"
    assert v.fix_hint is not None
    assert "single relative source file path" in v.fix_hint
    assert "checked AC" in v.fix_hint


def test_references_invalid_dict_stays_not_auto_fixable():
    violations = _check(_BODY_REFS_INVALID_DICT_EXTRA, MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.reference_entry_invalid"
    assert v.auto_fixable is False


def test_unknown_key_without_milestone_label_when_yaml_present():
    violations = _check(BODY_WITH_INVALID_YAML, NON_MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.yaml_invalid"


def test_unparseable_yaml_without_milestone_label_when_yaml_present():
    violations = _check(BODY_WITH_UNPARSEABLE_YAML, NON_MILESTONE_LABELS)
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.yaml_invalid"


# --- paths_must_exist / paths_must_not_exist entry shape (#4803) ---


def _paths_body(key: str, entries_yaml: str) -> str:
    return (
        "## Acceptance Criteria\n\n```yaml\n"
        f"{key}:{entries_yaml}\n"
        "```\n\n- [ ] some check\n"
    )


def _path_entries_body(key: str) -> str:
    return _paths_body(
        key,
        '\n  - /abs/a.py\n  - ../a.py\n  - ""\n  - 1\n  - src/ok.py',
    )


def test_paths_must_exist_glob_and_plain_entries_no_violation():
    body = _paths_body("paths_must_exist", '\n  - "a/*.txt"\n  - src/b.py')
    assert _check(body, MILESTONE_LABELS) == []


def test_paths_must_exist_not_a_list():
    violations = _check(_paths_body("paths_must_exist", " a.py"), MILESTONE_LABELS)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "ac_format.path_entry_invalid"
    assert v.location == "paths_must_exist"
    assert v.message == "paths_must_exist must be a list"


def test_paths_invalid_entry_without_milestone_label_when_yaml_present():
    violations = _check(_path_entries_body("paths_must_exist"), NON_MILESTONE_LABELS)
    assert len(violations) == 4


def test_paths_no_check_without_yaml_block_when_unstructured():
    assert _check(BODY_WITH_AC_NO_YAML, NON_MILESTONE_LABELS) == []


def _git_repo(tmp_path: Path) -> Path:
    """Refs sumipan/nexus#6431 — git repo with origin/main for base grep checks."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_grep_match_outside_allow_paths(tmp_path):
    """Refs sumipan/nexus#6431 — base grep hit outside allow_paths is reported."""
    repo = _git_repo(tmp_path)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("TOKEN = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
        capture_output=True,
    )
    entry = {"pattern": "TOKEN", "paths": ["pkg"], "exclude": []}
    violations = check_greps_against_base([entry], repo, "origin/main", ["other/**"])
    assert len(violations) == 1
    assert violations[0].rule_id == "ac_format.grep_outside_allow_paths"
    assert violations[0].location == "pkg/a.py"

    cleared = check_greps_against_base([entry], repo, "origin/main", ["pkg/**"])
    assert cleared == []

    excluded = check_greps_against_base(
        [{**entry, "exclude": ["pkg/a.py"]}],
        repo,
        "origin/main",
        ["other/**"],
    )
    assert excluded == []


def test_grep_entry_invalid():
    """Refs sumipan/nexus#6431 — invalid greps_must_be_empty entries fail ac_format."""
    bodies = [
        (
            "```yaml\ngreps_must_be_empty:\n  - pattern: x\n"
            "```"
        ),
        (
            "```yaml\ngreps_must_be_empty:\n  - paths:\n      - pkg\n"
            "```"
        ),
        (
            "```yaml\ngreps_must_be_empty:\n  - pattern: '('\n    paths:\n      - pkg\n"
            "```"
        ),
    ]
    template_missing_paths = """\
## Acceptance Criteria

{yaml}

- [ ] ac
"""
    for block in bodies:
        body = template_missing_paths.format(yaml=block)
        violations = _check(body, MILESTONE_LABELS)
        assert any(v.rule_id == "ac_format.grep_entry_invalid" for v in violations)


def test_greps_must_be_empty_is_allowed_key():
    """Refs sumipan/nexus#6431 — greps_must_be_empty is an allowed AC YAML key."""
    body = """\
## Acceptance Criteria

```yaml
greps_must_be_empty:
  - pattern: foo
    paths:
      - src
paths_must_exist: []
paths_must_not_exist: []
```

- [ ] ac
"""
    violations = _check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "ac_format.yaml_invalid" for v in violations)
