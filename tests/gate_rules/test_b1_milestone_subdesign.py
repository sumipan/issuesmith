"""tests/gate_rules/test_b1_milestone_subdesign.py — unit tests for b1_milestone_subdesign gate."""
from __future__ import annotations

import pytest

import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: F401
from issuesmith.gate_rules import GATE_REGISTRY
from tests.legacy_text import CHANGE_TYPE, DEPENDENCY, DESCRIPTION, FILE_PATH, REPOSITORY, SUB

MILESTONE_LABELS = ["scope:milestone"]
NON_MILESTONE_LABELS = ["scope:feature"]
_TABLE_HEADER = f"{REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION}"


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_milestone_subdesign"]()
    return gate.check(body, labels)


# ASCII fixture data.
def _sub_block(
    num: int,
    title: str,
    path: str,
    *,
    repo: str = "sumipan/nexus",
    change_type: str = "Add",
    ac_items: list[str] | None = None,
) -> str:
    if ac_items is None:
        ac_items = [
            f"Sub{num} c306E_Acceptance Criteria_c9805_c76EE alpha",
            f"Sub{num} c306E_Acceptance Criteria_c9805_c76EE beta",
            f"Sub{num} c306E_Acceptance Criteria_c9805_c76EE gamma",
        ]
    ac_lines = "\n".join(f"- [ ] {item}" for item in ac_items)
    return f"""\
#### {SUB}{num}: {title}

**Scope**: Sub{num} c306E_c5B9F_c88C5_c7BC4_c56F2_c3092_c5177_c4F53_c5316
**Design Policy**: Sub{num} c306E_Design Policy
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `{repo}` | `{path}` | {change_type} | Description |

**Acceptance Criteria**:
{ac_lines}
"""


# ASCII fixture data.
def _valid_body(*, sub2_path: str = "tools/foo/b.py") -> str:
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## Design

{_sub_block(1, "foo", "tools/foo/a.py")}
{_sub_block(2, "bar", sub2_path)}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | scope1 | None |
| 2 | bar | scope2 | 1 |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | add a |
| `sumipan/nexus` | `{sub2_path}` | Add | add b |

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/a.py
  - {sub2_path}
```
"""


# Drift fixture from milestone/30 (#1827) — intentionally includes 7 violation categories
# ASCII fixture data.
MILESTONE30_DRIFT_BODY = f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - docs/MLTGNT-ROADMAP.md
```

## Design

{_sub_block(1, "Phase A", "skills/_template/README.md")}
#### {SUB}2: Phase B

**Scope**: SKILL.md c624B_c9806_c66F8_c5316
**Design Policy**: c547D_c4EE4_c5F62_c3067_c8A18_c8FF0
**Changed Files**:
| {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |
|---|---|---|
| `skills/mltgnt-skill/SKILL.md` | Modify | c30AC_c30A4_c30C9_c30E9_c30A4_c30F3_c8FFD_c52A0 |

**Acceptance Criteria**:
- [ ] result works correctly
- [ ] c30AC_c30A4_c30C9_c30E9_c30A4_c30F3_c304C_c5B58_c5728_c3059_c308B

#### {SUB}3: Phase D

**Scope**: runner c62E1_c5F35
**Design Policy**: SkillRunResult c8FFD_c52A0
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/mltgnt` | `src/mltgnt/skill/runner.py` | Modify | c623B_c308A_c5024_Change |

**Acceptance Criteria**:
- [ ] runner.run c304C SkillRunResult c3092_c8FD4_c3059
- [ ] diagnostics c30D5_c30A3_c30FC_c30EB_c30C9_c304C_c5B58_c5728_c3059_c308B
- [ ] c65E2_c5B58_c30C6_c30B9_c30C8_c304C_c901A_c308B

"""
# ASCII fixture data.
MILESTONE30_DRIFT_BODY += f"""\
## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | Phase A | README c898F_c7D04 | None |
| 2 | Phase B | c624B_c9806_c66F8_c5316 | 1 |
| 3 | Phase D | runner c62E1_c5F35 | 1 |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `docs/MLTGNT-ROADMAP.md` | Modify | c72B6_c614B_c66F4_c65B0 |

## Impact Survey

| c30D5_c30A1_c30A4_c30EB | c53C2_c7167_c7B87_c6240 | c5F71_c97FF |
|---------|---------|------|
| `mltgnt/src/mltgnt/skill/runner.py` | run() c623B_c308A_c5024 | c578B_Change |

## Acceptance Criteria

```yaml
paths_must_exist:
  - skills/_template/README.md
```
"""


def test_no_milestone_label_returns_empty():
    assert _check(_valid_body(), NON_MILESTONE_LABELS) == []


def test_valid_body_passes():
    assert _check(_valid_body(), MILESTONE_LABELS) == []


def test_sub_count_mismatch():
    body = _valid_body().replace("| 2 | bar | scope2 | 1 |", "")
    violations = _check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.sub_count_mismatch" for v in violations)


def test_sub_count_mismatch_fix_hint_lists_required_subsections():
    from issuesmith.config import get_config
    from tests.legacy_text import SUB

    body = _valid_body().replace("| 2 | bar | scope2 | 1 |", "")
    violation = next(
        v for v in _check(body, MILESTONE_LABELS)
        if v.rule_id == "b1_milestone_subdesign.sub_count_mismatch"
    )
    assert violation.fix_hint is not None
    assert get_config().sections["design"] in violation.fix_hint
    for name in get_config().sub_design_subsections:
        assert name in violation.fix_hint
    assert violation.auto_fixable is False
    assert f"#### {SUB}N:" in violation.fix_hint
    assert "add `#### Sub" not in (violation.fix_hint or "")
    assert "#### Sub N:" not in violation.fix_hint
    assert "#### Sub " not in violation.fix_hint


def test_promoted_body_plan_rows_match_sub_headers_and_file_union():
    """AC (#4191): plan rows, SUB_HEADER_RE count, and parent/sub path union agree."""
    from issuesmith.config import get_config
    from issuesmith.contract import SUB_HEADER_RE, extract_change_table_rows, get_section
    from issuesmith.gate_rules.b1_milestone_subdesign import _count_sub_plan_rows
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    body = _oversized_non_milestone_body()
    promoted = promote_oversized_issue_body(body)
    sections = get_config().sections
    plan_count = _count_sub_plan_rows(promoted)
    design = get_section(promoted, sections["design"]) or ""
    header_count = len(SUB_HEADER_RE.findall(design))
    assert plan_count == header_count
    assert plan_count is not None and plan_count >= 3
    parent = {
        path
        for _, path, _ in extract_change_table_rows(
            get_section(promoted, sections["changed_files"]) or ""
        )
    }
    sub_paths: set[str] = set()
    from issuesmith.gate_rules.b1_milestone_subdesign import extract_sub_blocks

    for _, block in extract_sub_blocks(promoted):
        sub_paths.update(path for _, path, _ in extract_change_table_rows(block))
    assert parent == sub_paths
    assert "#### Sub " not in promoted


def _oversized_non_milestone_body() -> str:
    """Non-milestone body that trips scope_size (3+ concerns, many files)."""
    from tests.legacy_text import CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY

    header = f"| {REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |"
    rows = [
        ("src/a/f1.py", "Modify"),
        ("src/a/f2.py", "Modify"),
        ("src/a/f3.py", "Modify"),
        ("src/b/f1.py", "Modify"),
        ("src/b/f2.py", "Modify"),
        ("src/b/f3.py", "Modify"),
        ("src/c/f1.py", "Modify"),
        ("src/c/f2.py", "Modify"),
        ("src/c/f3.py", "Modify"),
    ]
    table = "\n".join(
        f"| `sumipan/nexus` | `{path}` | {kind} | x |" for path, kind in rows
    )
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```

## Design
single-issue design before promotion

## Changed Files
{header}
|---|---|---|---|
{table}
"""


def test_subsection_missing():
    # ASCII fixture data.
    body = _valid_body().replace("**Design Policy**: Sub1 c306E_Design Policy", "")
    violations = _check(body, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.subsection_missing" for v in violations)


def test_table_schema_three_columns():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.table_schema" for v in violations)


def test_repo_mismatch_mltgnt():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.repo_mismatch" for v in violations)


def test_file_union_missing_in_parent():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(
        v.rule_id == "b1_milestone_subdesign.file_union_missing_in_parent"
        for v in violations
    )


def test_ac_vague_word():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.ac_vague_word" for v in violations)


def test_ac_count_too_few():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.ac_count" for v in violations)


def test_impact_scope_pollution():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    assert any(v.rule_id == "b1_milestone_subdesign.impact_scope_pollution" for v in violations)


def test_repo_mismatch_auto_fixable():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    repo_v = next(v for v in violations if v.rule_id == "b1_milestone_subdesign.repo_mismatch")
    assert repo_v.auto_fixable is True
    assert "target_repo:" in (repo_v.fix_hint or "")


def test_registered_in_gate_registry():
    assert "b1_milestone_subdesign" in GATE_REGISTRY


def test_milestone30_drift_detects_multiple_categories():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    rule_ids = {v.rule_id for v in violations}
    expected = {
        "b1_milestone_subdesign.table_schema",
        "b1_milestone_subdesign.repo_mismatch",
        "b1_milestone_subdesign.file_union_missing_in_parent",
        "b1_milestone_subdesign.ac_vague_word",
        "b1_milestone_subdesign.ac_count",
        "b1_milestone_subdesign.impact_scope_pollution",
    }
    assert expected.issubset(rule_ids)


# --- Two-repo milestone parent: one ```yaml block per repo (nexus #4076) ---


def _two_repo_body(*, with_parent_changed_files: bool) -> str:
    parent_changed = f"""\
## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | add a |
| `sumipan/ghdag` | `src/ghdag/b.py` | Add | add b |

""" if with_parent_changed_files else ""
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/a.py
```

```yaml
target_repo: sumipan/ghdag
base_branch: main
allow_paths:
  - src/ghdag/b.py
```

## Design

{_sub_block(1, "foo", "tools/foo/a.py")}
{_sub_block(2, "bar", "src/ghdag/b.py", repo="sumipan/ghdag")}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | scope1 | None |
| 2 | bar | scope2 | 1 |

{parent_changed}## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/a.py
```
"""


def test_allowed_repos_unions_every_metadata_block():
    from issuesmith.gate_rules.b1_milestone_subdesign import _allowed_repos

    body = _two_repo_body(with_parent_changed_files=True)
    assert _allowed_repos(body) == {"sumipan/nexus", "sumipan/ghdag"}


def test_allowed_repos_adds_diary_from_any_block():
    from issuesmith.gate_rules.b1_milestone_subdesign import _allowed_repos

    body = (
        "```yaml\ntarget_repo: sumipan/nexus\n```\n\n"
        "```yaml\ntarget_repo: sumipan/ghdag\ndiary_allow_paths:\n  - notes/a.md\n```\n"
    )
    assert _allowed_repos(body) == {"sumipan/nexus", "sumipan/ghdag", "sumipan/diary"}


def test_two_repo_milestone_passes():
    violations = _check(_two_repo_body(with_parent_changed_files=True), MILESTONE_LABELS)
    assert violations == []


def test_two_repo_milestone_has_no_repo_mismatch():
    violations = _check(_two_repo_body(with_parent_changed_files=False), MILESTONE_LABELS)
    assert not any(v.rule_id == "b1_milestone_subdesign.repo_mismatch" for v in violations)


def test_file_union_falls_back_to_allow_paths_without_parent_changed_files():
    violations = _check(_two_repo_body(with_parent_changed_files=False), MILESTONE_LABELS)
    assert not any(
        v.rule_id == "b1_milestone_subdesign.file_union_missing_in_parent" for v in violations
    )
    assert violations == []


def test_file_union_fallback_still_reports_paths_outside_allow_paths():
    body = _two_repo_body(with_parent_changed_files=False).replace(
        "  - src/ghdag/b.py\n", "  - src/ghdag/other.py\n"
    )
    violations = _check(body, MILESTONE_LABELS)
    [v] = [v for v in violations if v.rule_id.endswith("file_union_missing_in_parent")]
    assert "src/ghdag/b.py" in v.message


def test_repo_mismatch_fix_hint_adds_a_block_instead_of_rewriting():
    violations = _check(MILESTONE30_DRIFT_BODY, MILESTONE_LABELS)
    repo_v = next(v for v in violations if v.rule_id == "b1_milestone_subdesign.repo_mismatch")
    hint = repo_v.fix_hint or ""
    assert "target_repo: sumipan/mltgnt" in hint
    assert "add" in hint.lower()
    assert "keep" in hint.lower()


# --- R2: iter_sub_blocks / sub_block / extract_sub_blocks endpoint tests ---


def _body_with_parent_section_after_last_sub() -> str:
    """Milestone parent body where the last sub is followed by a parent ### Changed Files."""
    from issuesmith.config import get_config

    sections = get_config().sections
    changed = sections["changed_files"]
    sub1 = _sub_block(1, "alpha", "tools/foo/a.py")
    sub2 = _sub_block(2, "beta", "tools/foo/b.py")
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## {sections["design"]}

{sub1}
{sub2}

### {changed}
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | a |
| `sumipan/nexus` | `tools/foo/b.py` | Add | b |

## {sections["milestone"]}

### {sections["sub_plan"]}
| # | Title | Content | Dependency |
|---|--------|---------|------------|
| 1 | alpha | scope1 | None |
| 2 | beta | scope2 | 1 |

## {changed}
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | a |
| `sumipan/nexus` | `tools/foo/b.py` | Add | b |

## {sections["acceptance_criteria"]}

```yaml
paths_must_exist:
  - tools/foo/a.py
  - tools/foo/b.py
```
"""


def test_r2_file_union_no_duplicate_when_parent_section_follows_last_sub():
    """Regression: parent ### Changed Files after last sub must not cause file_union_duplicate."""
    body = _body_with_parent_section_after_last_sub()
    violations = _check(body, MILESTONE_LABELS)
    dup_violations = [v for v in violations if v.rule_id == "b1_milestone_subdesign.file_union_duplicate"]
    assert dup_violations == [], dup_violations


def test_iter_sub_blocks_stops_at_h3():
    """iter_sub_blocks: last block ends before a following ### heading."""
    from issuesmith.contract import iter_sub_blocks

    text = (
        f"#### {SUB}1: alpha\n"
        "content1\n"
        f"#### {SUB}2: beta\n"
        "content2\n"
        "### Parent Section\n"
        "parent content\n"
    )
    blocks = iter_sub_blocks(text)
    assert len(blocks) == 2
    _, block2 = blocks[1]
    assert "Parent Section" not in block2


def test_iter_sub_blocks_does_not_cut_on_bold_or_h4_plus():
    """iter_sub_blocks: bold labels and #### headings do not terminate a block."""
    from issuesmith.contract import iter_sub_blocks

    sub1 = _sub_block(1, "alpha", "tools/foo/a.py")
    sub2 = _sub_block(2, "beta", "tools/foo/b.py")
    text = sub1 + sub2
    blocks = iter_sub_blocks(text)
    assert len(blocks) == 2
    _, block1 = blocks[0]
    assert "**Changed Files**" in block1
    assert "tools/foo/a.py" in block1


def test_iter_sub_blocks_empty_when_no_subs():
    """iter_sub_blocks: returns [] when the input contains no sub headers."""
    from issuesmith.contract import iter_sub_blocks

    assert iter_sub_blocks("") == []
    assert iter_sub_blocks("## Design\nno subs here\n") == []


def test_sub_block_returns_empty_when_no_subs():
    """sub_block: returns '' when no sub headers exist."""
    from issuesmith.contract import sub_block

    assert sub_block("## Design\nno subs\n", 1) == ""


def test_sub_block_parity_with_extract_sub_blocks():
    """sub_block(body, N) and extract_sub_blocks(body)[N-1] return identical text."""
    from issuesmith.contract import sub_block
    from issuesmith.gate_rules.b1_milestone_subdesign import extract_sub_blocks

    body = _body_with_parent_section_after_last_sub()
    blocks = extract_sub_blocks(body)
    assert len(blocks) == 2
    _, eb2 = blocks[1]
    sb2 = sub_block(body, 2)
    assert sb2 == eb2


def test_sub_block_parity_excludes_parent_section_paths():
    """change_paths_for_repo on sub_block(body, 2) must not include parent-only paths."""
    from issuesmith.contract import change_paths_for_repo, sub_block

    body = _body_with_parent_section_after_last_sub()
    block2 = sub_block(body, 2)
    paths = change_paths_for_repo(block2, "sumipan/nexus")
    assert "tools/foo/a.py" not in paths, "parent-only path leaked into sub2 block"
    assert "tools/foo/b.py" in paths


# --- b1_verify oscillation detection (nexus #4076) ---


def _violation(rule_id: str, severity: str = "fail"):
    from ghdag.workflow.gates import Violation

    return Violation(
        rule_id=rule_id,
        severity=severity,
        message="m",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )


_PREV_REPORT = (
    "VERIFY_FAILED_CHECKS: b1_milestone_subdesign.repo_mismatch"
    " b1_milestone_subdesign.repo_mismatch scope_breadth.too_large\n"
    "\n## b1_milestone_subdesign.repo_mismatch\nmsg\n"
)


def test_parse_prev_counts_reads_the_report_header():
    from issuesmith.b1_verify import _parse_prev_counts

    counts = _parse_prev_counts(_PREV_REPORT)
    assert counts == {
        "b1_milestone_subdesign.repo_mismatch": 2,
        "scope_breadth.too_large": 1,
    }
    assert _parse_prev_counts("VERIFY_FAILED_CHECKS: (none)\n") == {}
    assert _parse_prev_counts("") == {}


def test_oscillation_detected_when_a_rule_count_grows():
    from issuesmith.b1_verify import detect_oscillation

    current = [_violation("b1_milestone_subdesign.repo_mismatch")] * 3
    v = detect_oscillation(current, _PREV_REPORT)
    assert v is not None
    assert v.rule_id == "b1_verify.oscillation_detected"
    assert v.severity == "fail"
    assert "b1_milestone_subdesign.repo_mismatch" in v.message


def test_no_oscillation_when_counts_shrink_or_stay():
    from issuesmith.b1_verify import detect_oscillation

    current = [_violation("b1_milestone_subdesign.repo_mismatch")] * 2
    assert detect_oscillation(current, _PREV_REPORT) is None
    assert detect_oscillation([], _PREV_REPORT) is None


def test_main_prev_report_appends_oscillation(tmp_path, monkeypatch, capsys):
    import sys
    import types

    import issuesmith.b1_verify as b1_verify

    prev = tmp_path / "prev.txt"
    prev.write_text(_PREV_REPORT, encoding="utf-8")
    forge = types.SimpleNamespace(
        issue_get=lambda *_a, **_k: {"body": "x", "labels": [{"name": "scope:milestone"}]}
    )
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: forge)
    monkeypatch.setattr(
        b1_verify,
        "collect_violations",
        lambda body, labels: [_violation("b1_milestone_subdesign.repo_mismatch")] * 5,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "b1_verify",
            "4048",
            "--no-apply-deterministic",
            "--prev-report",
            str(prev),
        ],
    )
    assert b1_verify.main() == 1
    out = capsys.readouterr().out
    assert "b1_verify.oscillation_detected" in out.splitlines()[0]


def test_main_without_prev_report_has_no_oscillation(monkeypatch, capsys):
    import sys
    import types

    import issuesmith.b1_verify as b1_verify

    forge = types.SimpleNamespace(issue_get=lambda *_a, **_k: {"body": "x", "labels": []})
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: forge)
    monkeypatch.setattr(
        b1_verify,
        "collect_violations",
        lambda body, labels: [_violation("b1_milestone_subdesign.repo_mismatch")],
    )
    monkeypatch.setattr(
        sys, "argv", ["b1_verify", "4048", "--no-apply-deterministic"]
    )
    assert b1_verify.main() == 1
    assert "oscillation" not in capsys.readouterr().out


# --- Language pack vocabulary (nexus #4474) ---

# ASCII vocabulary that differs from the EN pack defaults.
_ASCII_PACK = {
    "sub_header_prefix": "Part",
    "change_table_columns": ["Repo", "Path", "Kind", "Note"],
    "vague_ac_words": ["mostly fine"],
}


def _use_pack(tmp_path, monkeypatch, **overrides) -> None:
    """Point the config at a language pack YAML: the EN pack with ``overrides``."""
    import dataclasses

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.language import EN

    data = {f.name: getattr(EN, f.name) for f in dataclasses.fields(EN)}
    data.update(overrides)
    plain = {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}
    plain["sections"] = dict(EN.sections)
    plain["messages"] = dict(EN.messages)
    pack_path = tmp_path / "language_pack.yaml"
    pack_path.write_text(yaml.safe_dump(plain), encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith", "language_pack": str(pack_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _in_vocabulary(body: str, prefix: str, columns, vague: str) -> str:
    return (
        body.replace(f"#### {SUB}", f"#### {prefix}")
        .replace(_TABLE_HEADER, " | ".join(columns))
        .replace("VAGUE", vague)
    )


def test_en_pack_sub_header_and_change_table_pass(tmp_path, monkeypatch):
    _use_pack(tmp_path, monkeypatch)
    body = _in_vocabulary(
        _valid_body(),
        "Sub",
        ["Repository", "File path", "Change type", "Description"],
        "",
    )
    assert "#### Sub1:" in body
    assert "| Repository | File path | Change type | Description |" in body
    assert _check(body, MILESTONE_LABELS) == []


def _vague_ac_body() -> str:
    return _valid_body().replace("Sub1 c306E_Acceptance Criteria_c9805_c76EE alpha", "VAGUE alpha")


def _bad_columns_body() -> str:
    return _valid_body().replace(_TABLE_HEADER, "A | B | C | D", 1)


@pytest.mark.parametrize(
    "make_body",
    [_valid_body, _vague_ac_body, _bad_columns_body],
    ids=["valid", "vague_ac", "bad_columns"],
)
def test_ascii_pack_matches_en_decision(tmp_path, monkeypatch, make_body):
    from issuesmith.language import EN

    en_dir = tmp_path / "en"
    en_dir.mkdir()
    _use_pack(en_dir, monkeypatch)
    en_body = _in_vocabulary(make_body(), "Sub", EN.change_table_columns, EN.vague_ac_words[0])
    en_ids = sorted(v.rule_id for v in _check(en_body, MILESTONE_LABELS))

    ascii_dir = tmp_path / "ascii"
    ascii_dir.mkdir()
    _use_pack(ascii_dir, monkeypatch, **_ASCII_PACK)
    ascii_body = _in_vocabulary(
        make_body(), "Part", _ASCII_PACK["change_table_columns"], "mostly fine"
    )
    ascii_ids = sorted(v.rule_id for v in _check(ascii_body, MILESTONE_LABELS))

    assert ascii_ids == en_ids
    if make_body is _vague_ac_body:
        assert "b1_milestone_subdesign.ac_vague_word" in en_ids
    if make_body is _bad_columns_body:
        assert "b1_milestone_subdesign.table_schema" in en_ids


def test_ascii_pack_rejects_en_vocabulary(tmp_path, monkeypatch):
    """With the ASCII pack, EN sub headers are not sub blocks (vocabulary is read from it)."""
    from issuesmith.language import EN

    _use_pack(tmp_path, monkeypatch, **_ASCII_PACK)
    body = _in_vocabulary(_valid_body(), "Sub", EN.change_table_columns, "")
    rule_ids = {v.rule_id for v in _check(body, MILESTONE_LABELS)}
    assert "b1_milestone_subdesign.sub_count_mismatch" in rule_ids


# ---------------------------------------------------------------------------
# #4518 — milestone splits that P1 cannot implement
# ---------------------------------------------------------------------------

_ORPHAN_RULE = "b1_milestone_subdesign.deletion_reference_orphan"
_SIBLING_TEST_RULE = "b1_milestone_subdesign.behavior_test_in_sibling"
_CONTRADICTION_RULE = "b1_milestone_subdesign.sub_ac_contradiction"

_STANDALONE_IMPORT_ERROR = "This sub alone raises ImportError in test collection."
_PARENT_FULL_PASS = "The full test pass is checked by the parent."
_EXISTING_TESTS_PASS = "All existing tests pass"


def _rule_ids(violations) -> list[str]:
    return [v.rule_id for v in violations]


def _milestone_body(
    subs: list[tuple[str, list[tuple[str, str]]]],
    *,
    allow_paths: tuple[str, ...] = ("docs/**",),
) -> str:
    """Milestone parent body; ``subs`` is ``[(design policy, [(path, change type)])]``."""
    blocks: list[str] = []
    plan_rows: list[str] = []
    for num, (policy, rows) in enumerate(subs, start=1):
        table = "\n".join(
            f"| `sumipan/nexus` | `{path}` | {change_type} | Description |"
            for path, change_type in rows
        )
        blocks.append(f"""\
#### {SUB}{num}: sub{num}

**Scope**: scope {num}
**Design Policy**: {policy}
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
{table}

**Acceptance Criteria**:
- [ ] Sub{num} alpha
- [ ] Sub{num} beta
- [ ] Sub{num} gamma
""")
        plan_rows.append(f"| {num} | sub{num} | scope{num} | None |")
    allow = "\n".join(f"  - {p}" for p in allow_paths)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
{allow}
```

## Design

{chr(10).join(blocks)}
## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
{chr(10).join(plan_rows)}
"""


def _git_repo(root, files: dict[str, str]):
    import subprocess

    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=root, check=True)
    return root


@pytest.fixture
def deletion_repo(tmp_path, monkeypatch):
    """Clone with ``old.py`` imported by ``user.py``; resolve_scope_root points at it."""
    root = _git_repo(tmp_path / "repo", {
        "src/pkg/old.py": "VALUE = 1\n",
        "src/pkg/user.py": "from pkg.old import VALUE\n",
    })
    monkeypatch.setattr(
        "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
        lambda metadata, cfg: root,
    )
    return root


def test_deletion_reference_orphan_when_no_sub_covers_referrer(deletion_repo):
    body = _milestone_body([
        ("drop old", [("src/pkg/old.py", "Delete")]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    orphans = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _ORPHAN_RULE]
    assert len(orphans) == 1
    assert "src/pkg/old.py" in orphans[0].message
    assert "src/pkg/user.py" in orphans[0].message
    assert orphans[0].fix_hint


def test_deletion_reference_orphan_passes_when_sibling_covers_referrer(deletion_repo):
    body = _milestone_body([
        ("drop old", [("src/pkg/old.py", "Delete")]),
        ("follow", [("src/pkg/user.py", "Modify")]),
    ])
    assert _ORPHAN_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_deletion_reference_orphan_skipped_without_clone(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
        lambda metadata, cfg: None,
    )
    monkeypatch.setattr(
        "issuesmith.gate_rules.scope_coupling.deletion_references_for_body",
        lambda body: calls.append(body) or {"src/pkg/old.py": ["src/pkg/user.py"]},
    )
    body = _milestone_body([
        ("drop old", [("src/pkg/old.py", "Delete")]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    assert _ORPHAN_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))
    assert calls == []


def test_deletion_reference_orphan_not_run_without_delete_rows(monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("must not resolve the clone without delete rows")

    monkeypatch.setattr("issuesmith.gate_rules.scope_coupling.resolve_scope_root", _boom)
    body = _milestone_body([
        ("add", [("src/pkg/new.py", "Add")]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    assert _ORPHAN_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_behavior_test_in_sibling_detected():
    body = _milestone_body([
        ("impl", [("src/mltgnt/config/language.py", "Modify")]),
        ("tests", [("tests/config/test_language.py", "Modify")]),
    ])
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _SIBLING_TEST_RULE]
    assert len(hits) == 1
    assert "src/mltgnt/config/language.py" in hits[0].message
    assert "tests/config/test_language.py" in hits[0].message
    assert hits[0].location == f"#### {SUB}1"
    assert hits[0].fix_hint


def test_behavior_test_in_same_sub_passes():
    body = _milestone_body([
        ("impl", [
            ("src/mltgnt/config/language.py", "Modify"),
            ("tests/config/test_language.py", "Modify"),
        ]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    assert _SIBLING_TEST_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_behavior_test_in_sibling_ignores_partial_stem_match():
    body = _milestone_body([
        ("impl", [("src/pkg/a.py", "Modify")]),
        ("tests", [("tests/test_data.py", "Modify")]),
    ])
    assert _SIBLING_TEST_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def _contradiction_body(policy: str, ac_items: list[str]) -> str:
    block = _sub_block(1, "drop", "tools/foo/a.py", ac_items=ac_items).replace(
        "**Design Policy**: Sub1 c306E_Design Policy", f"**Design Policy**: {policy}"
    )
    return _valid_body().replace(_sub_block(1, "foo", "tools/foo/a.py"), block)


_PLAIN_AC = ["Sub1 alpha", "Sub1 beta", "Sub1 gamma"]


def test_sub_ac_contradiction_detected():
    body = _contradiction_body(
        f"{_STANDALONE_IMPORT_ERROR} {_PARENT_FULL_PASS}",
        [*_PLAIN_AC, _EXISTING_TESTS_PASS],
    )
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _CONTRADICTION_RULE]
    assert len(hits) == 1
    assert hits[0].location == f"#### {SUB}1"
    assert hits[0].fix_hint


def test_sub_ac_contradiction_needs_both_conditions():
    only_a = _contradiction_body(_STANDALONE_IMPORT_ERROR, _PLAIN_AC)
    only_b = _contradiction_body("plain policy", [*_PLAIN_AC, _EXISTING_TESTS_PASS])
    assert _CONTRADICTION_RULE not in _rule_ids(_check(only_a, MILESTONE_LABELS))
    assert _CONTRADICTION_RULE not in _rule_ids(_check(only_b, MILESTONE_LABELS))


def test_new_checks_skip_non_milestone(deletion_repo):
    bodies = [
        _milestone_body([
            ("drop old", [("src/pkg/old.py", "Delete")]),
            ("tests", [("tests/pkg/test_user.py", "Modify")]),
            ("impl", [("src/pkg/user.py", "Modify")]),
        ]),
        _contradiction_body(_STANDALONE_IMPORT_ERROR, [*_PLAIN_AC, _EXISTING_TESTS_PASS]),
    ]
    for body in bodies:
        assert _check(body, NON_MILESTONE_LABELS) == []


# ---------------------------------------------------------------------------
# #4742 — sub design execution-order text vs split-plan dependency column
# ---------------------------------------------------------------------------

_ORDER_DEP_RULE = "b1_milestone_subdesign.sub_execution_order_dep"
_AFTER_COMPLETE = chr(0x5B8C) + chr(0x4E86) + chr(0x540E)


def _order_dep_body(
    *,
    sub2_policy: str,
    sub2_dep: str = "None",
) -> str:
    sub1 = _sub_block(1, "foo", "tools/foo/a.py")
    sub2 = _sub_block(2, "bar", "tools/foo/b.py").replace(
        "**Design Policy**: Sub2 c306E_Design Policy",
        f"**Design Policy**: {sub2_policy}",
    )
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## Design

{sub1}
{sub2}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | {DEPENDENCY} |
|---|--------|------|------|
| 1 | foo | scope1 | None |
| 2 | bar | scope2 | {sub2_dep} |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | add a |
| `sumipan/nexus` | `tools/foo/b.py` | Add | add b |

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/a.py
  - tools/foo/b.py
```
"""


def test_sub_execution_order_dep_detected_when_plan_dep_missing():
    body = _order_dep_body(sub2_policy=f"{SUB}1 complete before this sub starts")
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _ORDER_DEP_RULE]
    assert len(hits) == 1
    assert "Sub 2" in hits[0].message
    assert "Sub 1" in hits[0].message
    assert hits[0].location == f"#### {SUB}2"
    assert hits[0].auto_fixable is True
    assert "#1" in (hits[0].fix_hint or "")


def test_sub_execution_order_dep_detected_with_cjk_phrase():
    body = _order_dep_body(sub2_policy=f"{SUB}1 {_AFTER_COMPLETE}")
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _ORDER_DEP_RULE]
    assert len(hits) == 1


def test_sub_execution_order_dep_passes_when_plan_dep_lists_ref():
    body = _order_dep_body(
        sub2_policy=f"{SUB}1 merged before this sub starts",
        sub2_dep="#1",
    )
    assert _ORDER_DEP_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_sub_execution_order_dep_no_indicator():
    assert _ORDER_DEP_RULE not in _rule_ids(_check(_valid_body(), MILESTONE_LABELS))


def test_sub_execution_order_dep_ignores_self_reference():
    body = _order_dep_body(sub2_policy=f"{SUB}2 complete on its own")
    assert _ORDER_DEP_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_sub_execution_order_dep_skips_non_milestone():
    body = _order_dep_body(sub2_policy=f"{SUB}1 complete before this sub starts")
    assert _check(body, NON_MILESTONE_LABELS) == []
