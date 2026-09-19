"""tests/gate_rules/test_cp1_milestone.py — unit tests for CP1 milestone checks 8–11."""
from __future__ import annotations

from issuesmith.gate_rules.cp1 import Cp1Rules, _keyword_tokens
from tests.legacy_text import (
    ADD,
    CHANGE_TYPE,
    DESCRIPTION,
    FILE_PATH,
    MODIFY,
    OPTIONAL_PREFIX,
    REPOSITORY,
    SUB,
)

MILESTONE_LABELS = ["scope:milestone"]
_TABLE_HEADER = f"{REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION}"


# ASCII fixture data.
def _sub_block(num: int, *, with_yaml: bool = True, extra_text: str = "") -> str:
    yaml_block = """\
```yaml
paths_must_exist:
  - tools/foo/a.py
```
""" if with_yaml else ""
    return f"""\
#### {SUB}{num}: foo

**Scope**: scope
**Design Policy**: plan
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | {ADD} | add

**Acceptance Criteria**:
{yaml_block}- [ ] Sub{num} alpha c9805_c76EE_c30C6_c30B9_c30C8
- [ ] Sub{num} beta c9805_c76EE_c30C6_c30B9_c30C8
- [ ] Sub{num} gamma c9805_c76EE_c30C6_c30B9_c30C8
{extra_text}
"""


# ASCII fixture data.
def _milestone_body(*, parent_ac: list[str] | None = None, sub_extra: str = "") -> str:
    if parent_ac is None:
        parent_ac = [
            "alpha acceptance check",
            "PR #9001 merged",
            "PR #9002 merged",
        ]
    parent_ac_lines = "\n".join(f"- [ ] {item}" for item in parent_ac)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## Design

{_sub_block(1, extra_text=sub_extra)}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | scope | None |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | {ADD} | add

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/a.py
```

{parent_ac_lines}
"""


def test_check8_sub_block_todo():
    # ASCII fixture data.
    body = _milestone_body(sub_extra="TODO: c5F8C_c3067_c6C7A_c3081_c308B\n")
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.forbidden_word.todo.sub1" for v in violations)


def test_check8_code_block_todo_excluded():
    body = _milestone_body(sub_extra="```python\n# TODO: ignore\n```\n")
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any("forbidden_word.todo" in v.rule_id for v in violations)


def test_check9_sub_ac_yaml_missing():
    body = _milestone_body()
    body = body.replace(
        "```yaml\npaths_must_exist:\n  - tools/foo/a.py\n```\n",
        "",
        1,
    )
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.sub_ac_yaml_missing" for v in violations)


def test_check9_sub_ac_yaml_present_passes():
    violations = Cp1Rules().check(_milestone_body(), MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.sub_ac_yaml_missing" for v in violations)


def test_check10_parent_ac_orphan():
    # ASCII fixture data.
    body = _milestone_body(parent_ac=[
        "c5B64_c7ACB_c3057_c305F_c89AA_Acceptance Criteria_c30C8_c30FC_c30AF_c30F3 xyzunique",
        "Sub-issue_c3059_c3079_c3066_c306E_c5B9F_c88C5_c304C_c5B8C_c4E86_c3059_c308B",
        "c672C Issue c3092 close c3059_c308B",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_meta_pattern_passes():
    # ASCII fixture data.
    body = _milestone_body(parent_ac=[
        "PR #9999 merged",
        "PR #9998 created",
        "PR #9997 closed",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_keyword_tokens_ascii_separator_split():
    tokens = _keyword_tokens("audit/report posted")
    assert len(tokens) >= 2
    assert "audit" in tokens
    assert "report" in tokens


def test_keyword_tokens_english_phase_unchanged():
    assert _keyword_tokens("Phase 4:") == ["Phase"]


def test_check10_ascii_tokens_parent_ac_cover_passes():
    body = _milestone_body(
        parent_ac=[
            "audit/report posted",
            "PR #9001 merged",
            "PR #9002 merged",
        ],
        sub_extra="- [ ] report posted\n",
    )
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_keyword_cover_passes():
    # ASCII fixture data.
    body = _milestone_body(parent_ac=[
        "alpha acceptance check",
        "PR #9001 merged",
        "PR #9002 merged",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_optional_prefix_passes():
    # ASCII fixture data.
    body = _milestone_body(parent_ac=[
        OPTIONAL_PREFIX + "deferred integration",
        "PR #9001 merged",
        "PR #9002 merged",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check10_optional_prefix_midtext_fails():
    # ASCII fixture data.
    body = _milestone_body(parent_ac=[
        "ghdag " + OPTIONAL_PREFIX + " deferred integration",
        "PR #9001 merged",
        "PR #9002 merged",
    ])
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check11_paths_must_exist_unmapped():
    # ASCII fixture data.
    body = _milestone_body()
    body = body.replace(
        "## Acceptance Criteria\n\n```yaml\npaths_must_exist:\n  - tools/foo/a.py\n```",
        "## Acceptance Criteria\n\n```yaml\npaths_must_exist:\n  - tools/foo/a.py\n  - tools/missing/new.py\n```",
        1,
    )
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)


def test_check11_paths_must_exist_mapped_passes():
    violations = Cp1Rules().check(_milestone_body(), MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)


# ASCII fixture data.
def _sub_block_modify(num: int, *, path: str = "src/ghdag/pipeline/audit_query.py") -> str:
    return f"""\
#### {SUB}{num}: audit

**Scope**: scope
**Design Policy**: plan
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/ghdag` | `{path}` | {MODIFY} | add function

**Acceptance Criteria**:
```yaml
paths_must_exist:
  - {path}
```
- [ ] Sub{num} alpha c9805_c76EE_c30C6_c30B9_c30C8
- [ ] Sub{num} beta c9805_c76EE_c30C6_c30B9_c30C8
- [ ] Sub{num} gamma c9805_c76EE_c30C6_c30B9_c30C8
"""


# ASCII fixture data.
def _milestone_body_with_modify_path() -> str:
    return f"""\
```yaml
target_repo: sumipan/ghdag
base_branch: main
allow_paths:
  - src/ghdag/**
```

## Design

{_sub_block_modify(1)}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | audit | scope | None |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/ghdag` | `src/ghdag/pipeline/audit_query.py` | {MODIFY} | add function

## Acceptance Criteria

```yaml
paths_must_exist:
  - src/ghdag/pipeline/audit_query.py
```

- [ ] Sub-issue_c3059_c3079_c3066_c306E_c5B9F_c88C5_c304C_c5B8C_c4E86_c3059_c308B
- [ ] c672C Issue c3092 close c3059_c308B
"""


def test_check11_paths_must_exist_modify_mapped_passes():
    violations = Cp1Rules().check(_milestone_body_with_modify_path(), MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)


def test_milestone_checks_skipped_without_label():
    # ASCII fixture data.
    body = _milestone_body(sub_extra="TODO: c6B8B_c5B58\n")
    violations = Cp1Rules().check(body, [])
    assert not any("milestone" in v.rule_id for v in violations)
    assert not any(".sub1" in v.rule_id for v in violations)


# ASCII fixture data.
def _milestone_body_without_sub_blocks(*, parent_ac: list[str] | None = None) -> str:
    if parent_ac is None:
        parent_ac = [
            "c5B64_c7ACB_c3057_c305F_c89AA_Acceptance Criteria_c30C8_c30FC_c30AF_c30F3 xyzunique",
            "PR #9001 merged",
            "PR #9002 merged",
        ]
    parent_ac_lines = "\n".join(f"- [ ] {item}" for item in parent_ac)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## Design

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | scope | None |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | add

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/a.py
  - tools/missing/new.py
```

{parent_ac_lines}
"""


def test_check10_parent_ac_orphan_skipped_without_sub_blocks():
    body = _milestone_body_without_sub_blocks()
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.parent_ac_orphan" for v in violations)


def test_check11_paths_must_exist_unmapped_skipped_without_sub_blocks():
    body = _milestone_body_without_sub_blocks()
    violations = Cp1Rules().check(body, MILESTONE_LABELS)
    assert not any(v.rule_id == "cp1.milestone.paths_must_exist_unmapped" for v in violations)
