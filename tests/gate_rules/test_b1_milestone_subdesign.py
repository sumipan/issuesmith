"""tests/gate_rules/test_b1_milestone_subdesign.py — unit tests for b1_milestone_subdesign gate."""
from __future__ import annotations

import pytest

import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: F401
from issuesmith.config import get_config
from issuesmith.gate_rules import GATE_REGISTRY
from tests.legacy_text import CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY, SUB

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


# #4745: scripts/persona-note-articles.py vs tests/scripts/test_persona_note_articles.py
_HYPHEN_STEM_PAIRS = [
    ("scripts/persona-note-articles.py", "tests/scripts/test_persona_note_articles.py"),
    ("scripts/persona-sources-note.py", "tests/scripts/test_persona_sources_note.py"),
]


def _with_test_sub_dependency(body: str, dep: str) -> str:
    """Use the configured plan columns and set Sub2's depends-on cell to ``dep``."""
    num, title, _, content, depends_on = get_config().language.sub_plan_columns
    old_header = "| # | Title | c5185_c5BB9 | Dependency |"
    assert old_header in body
    body = body.replace(old_header, f"| {num} | {title} | {content} | {depends_on} |")
    return body.replace("| 2 | sub2 | scope2 | None |", f"| 2 | sub2 | scope2 | {dep} |")


@pytest.mark.parametrize(("impl_path", "test_path"), _HYPHEN_STEM_PAIRS)
def test_behavior_test_in_sibling_detects_hyphen_underscore_stem(impl_path, test_path):
    body = _with_test_sub_dependency(
        _milestone_body([
            ("impl", [(impl_path, "Add")]),
            ("tests", [(test_path, "Add")]),
        ]),
        get_config().language.no_deps_word,
    )
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _SIBLING_TEST_RULE]
    assert len(hits) == 1
    assert impl_path in hits[0].message
    assert test_path in hits[0].message
    assert hits[0].location == f"#### {SUB}1"


@pytest.mark.parametrize(("impl_path", "test_path"), _HYPHEN_STEM_PAIRS)
def test_behavior_test_in_sibling_matches_stem_case_insensitively(impl_path, test_path):
    body = _milestone_body([
        ("impl", [(impl_path.replace("persona", "Persona"), "Add")]),
        ("tests", [(test_path, "Add")]),
    ])
    assert _SIBLING_TEST_RULE in _rule_ids(_check(body, MILESTONE_LABELS))


@pytest.mark.parametrize("dep", ["#1", "1"])
@pytest.mark.parametrize(("impl_path", "test_path"), _HYPHEN_STEM_PAIRS)
def test_behavior_test_in_sibling_passes_when_test_sub_depends_on_impl(
    impl_path, test_path, dep
):
    body = _with_test_sub_dependency(
        _milestone_body([
            ("impl", [(impl_path, "Add")]),
            ("tests", [(test_path, "Add")]),
        ]),
        dep,
    )
    assert _SIBLING_TEST_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_behavior_test_in_sibling_dependency_on_other_sub_still_detected():
    body = _milestone_body([
        ("impl", [("scripts/persona-note-articles.py", "Add")]),
        ("tests", [("tests/scripts/test_persona_note_articles.py", "Add")]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    body = _with_test_sub_dependency(body, "#3")
    assert _SIBLING_TEST_RULE in _rule_ids(_check(body, MILESTONE_LABELS))


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


# --- #4853: parent change paths share change_paths_for_repo with readable check ---

_ROOT_FILE = "issuesmith.yaml"
def _skip_unless_root_file_paths() -> None:
    from issuesmith.contract import change_paths_for_repo

    probe = (
        f"| {_TABLE_HEADER} |\n|---|---|---|---|\n"
        f"| `sumipan/issuesmith` | `{_ROOT_FILE}` | Modify | x |\n"
    )
    if not change_paths_for_repo(probe):
        pytest.skip(
            "change_paths_for_repo() still drops repo-root files (#4797 sub1 not applied)"
        )


def _root_file_body() -> str:
    return f"""\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - {_ROOT_FILE}
  - src/issuesmith/a.py
```

## Design

{_sub_block(1, "root", _ROOT_FILE, repo="sumipan/issuesmith", change_type="Modify")}
{_sub_block(2, "src", "src/issuesmith/a.py", repo="sumipan/issuesmith")}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | root | scope1 | None |
| 2 | src | scope2 | 1 |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/issuesmith` | `{_ROOT_FILE}` | Modify | x |
| `sumipan/issuesmith` | `src/issuesmith/a.py` | Add | add a |
"""


def test_parent_change_paths_use_change_paths_for_repo():
    from issuesmith.contract import change_paths_for_repo, get_section
    from issuesmith.gate_rules.b1_milestone_subdesign import _extract_parent_change_paths

    body = _root_file_body()
    section = get_section(body, get_config().sections["changed_files"])
    assert _extract_parent_change_paths(body) == set(change_paths_for_repo(section))


def test_parent_change_paths_include_repo_root_file():
    _skip_unless_root_file_paths()
    from issuesmith.gate_rules.b1_milestone_subdesign import _extract_parent_change_paths

    assert _extract_parent_change_paths(_root_file_body()) == {
        _ROOT_FILE,
        "src/issuesmith/a.py",
    }


def test_root_file_only_sub_block_is_readable():
    _skip_unless_root_file_paths()
    assert _READABLE_RULE not in _rule_ids(_check(_root_file_body(), MILESTONE_LABELS))


_READABLE_RULE = "b1_milestone_subdesign.change_paths_unreadable"
_UNION_RULES = {
    "b1_milestone_subdesign.file_union_missing_in_subs",
    "b1_milestone_subdesign.file_union_missing_in_parent",
}


def test_file_union_and_readable_agree_on_repo_root_file():
    ids = set(_rule_ids(_check(_root_file_body(), MILESTONE_LABELS)))
    assert (_READABLE_RULE in ids) == bool(ids & _UNION_RULES)


# --- #4825: sibling new-file dependencies -------------------------------------------

_DEP_MISSING = "b1_milestone_subdesign.sibling_new_file_unreferenced_dependency"
_DEP_CYCLE = "b1_milestone_subdesign.dependency_cycle"
_REPO = "sumipan/issuesmith"


def _dep_sub(
    num: int,
    rows: list[tuple[str, str, str]],
    *,
    policy: str = "",
    ac_items: list[str] | None = None,
    repo: str = _REPO,
) -> str:
    """Sub block with ``rows`` of ``(path, change type, change content)``."""
    cfg = get_config()
    scope, design_policy, changed, ac = cfg.sub_design_subsections
    items = ac_items or [f"item alpha {num}", f"item beta {num}", f"item gamma {num}"]
    table = "\n".join(f"| `{repo}` | `{p}` | {k} | {c} |" for p, k, c in rows)
    ac_lines = "\n".join(f"- [ ] {item}" for item in items)
    return (
        f"#### {SUB}{num}: part {num}\n\n"
        f"**{scope}**: part {num}\n"
        f"**{design_policy}**: {policy or 'plain'}\n"
        f"**{changed}**:\n| {_TABLE_HEADER} |\n|---|---|---|---|\n{table}\n\n"
        f"**{ac}**:\n{ac_lines}\n"
    )


def _dep_body(blocks: list[str], deps: list[str]) -> str:
    cfg = get_config()
    plan_rows = "\n".join(
        f"| {i} | part {i} | {_REPO} | c | {dep} |" for i, dep in enumerate(deps, start=1)
    )
    return (
        f"```yaml\ntarget_repo: {_REPO}\nbase_branch: main\n```\n\n"
        f"## {cfg.sections['design']}\n\n" + "\n".join(blocks) + "\n"
        f"## {cfg.sections['milestone']}\n\n### {cfg.sections['sub_plan']}\n"
        f"{cfg.language.sub_plan_header}\n|---|---|---|---|---|\n{plan_rows}\n\n"
        "## Tail\nkept\n"
    )


def _projection_subs(sub2_policy: str = "build on `projection.py` from the creator") -> list[str]:
    """#4788 / #4807 / #4808 minimised: Sub 2 uses Sub 1's new projection.py."""
    return [
        _dep_sub(1, [
            ("src/issuesmith/projection.py", "add", "new projection module"),
            ("src/issuesmith/forge_guard.py", "add", "new guard"),
        ]),
        _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route issues")],
                 policy=sub2_policy),
    ]


def _cycle_subs() -> list[str]:
    """Sub 1 uses Sub 2's ops/labels.py while Sub 2 uses Sub 1's projection.py."""
    return [
        _dep_sub(1, [
            ("src/issuesmith/projection.py", "add", "new projection module"),
            ("src/issuesmith/andon.py", "modify", "call `ops/labels.py` project_issue"),
        ]),
        _dep_sub(2, [
            ("src/issuesmith/ops/labels.py", "add", "new label ops"),
            ("src/issuesmith/ops/dispatch.py", "modify", "call projection.render"),
        ]),
    ]


def _dep_ids(body: str) -> list[str]:
    return [v.rule_id for v in _check(body, MILESTONE_LABELS) if v.rule_id.startswith(
        (_DEP_MISSING, _DEP_CYCLE)
    )]


def test_infer_sub_dependencies_projection_fixture():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        extract_sub_blocks,
        infer_sub_dependencies,
    )

    body = _dep_body(_projection_subs(), ["none", "none"])
    assert infer_sub_dependencies(extract_sub_blocks(body)) == {1: set(), 2: {1}}
    violations = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _DEP_MISSING]
    assert len(violations) == 1
    (v,) = violations
    assert v.severity == "fail" and v.auto_fixable is True
    assert "Sub 2" in v.message and "Sub 1" in v.message
    assert "src/issuesmith/projection.py" in v.message


@pytest.mark.parametrize(
    "where",
    ["policy", "content", "ac"],
)
def test_infer_reads_policy_content_and_ac(where):
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        extract_sub_blocks,
        infer_sub_dependencies,
    )

    ref = "use projection here"
    sub2 = _dep_sub(
        2,
        [("src/issuesmith/ops/dispatch.py", "modify", ref if where == "content" else "route")],
        policy=ref if where == "policy" else "",
        ac_items=[ref, "b item", "c item"] if where == "ac" else None,
    )
    body = _dep_body([_projection_subs()[0], sub2], ["none", "none"])
    assert infer_sub_dependencies(extract_sub_blocks(body)) == {1: set(), 2: {1}}


def test_infer_is_deterministic():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        extract_sub_blocks,
        infer_sub_dependencies,
    )

    blocks = extract_sub_blocks(_dep_body(_cycle_subs(), ["none", "none"]))
    assert infer_sub_dependencies(blocks) == infer_sub_dependencies(blocks) == {1: {2}, 2: {1}}


@pytest.mark.parametrize(
    "case",
    ["self", "other_repo", "stem_partial", "url", "fence_lang", "path_cell", "duplicate_path",
     "declared"],
)
def test_no_false_missing_dependency(case):
    sub1 = _projection_subs()[0]
    deps = ["none", "none"]
    if case == "self":
        sub1 = _dep_sub(1, [("src/issuesmith/projection.py", "add", "see projection.py")],
                        policy="own `projection.py`")
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route")])
    elif case == "other_repo":
        sub2 = _dep_sub(2, [("src/x/dispatch.py", "modify", "use projection.py")],
                        policy="read `projection.py`", repo="sumipan/nexus")
    elif case == "stem_partial":
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "project projections")],
                        policy="projection_store and myprojection")
    elif case == "url":
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route")],
                        policy="see https://example.com/src/issuesmith/projection.py")
    elif case == "fence_lang":
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route")],
                        policy="snippet:\n```projection\nx = 1\n```")
    elif case == "path_cell":
        sub2 = _dep_sub(2, [("src/issuesmith/other/projection.py", "modify", "route")])
    elif case == "duplicate_path":
        sub2 = _dep_sub(2, [("src/issuesmith/projection.py", "modify", "tweak projection")])
    else:
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "use projection")])
        deps = ["none", "#1"]
    body = _dep_body([sub1, sub2], deps)
    assert _DEP_MISSING not in _dep_ids(body)


def test_declared_dependency_order_and_format_are_normalized():
    blocks = [*_projection_subs(), _dep_sub(3, [("src/issuesmith/z.py", "modify", "z")])]
    assert _DEP_MISSING not in _dep_ids(_dep_body(blocks, ["none", "3, #1", "none"]))


def test_dependency_cycle_is_reported():
    body = _dep_body(_cycle_subs(), ["none", "none"])
    violations = [
        v for v in _check(body, MILESTONE_LABELS) if v.rule_id in (_DEP_MISSING, _DEP_CYCLE)
    ]
    assert [v.rule_id for v in violations] == [_DEP_CYCLE]
    (cycle,) = violations
    assert cycle.severity == "fail" and cycle.auto_fixable is False
    assert "1 -> 2 -> 1" in cycle.message
    assert "merge" in cycle.fix_hint and "stub" in cycle.fix_hint


def test_declared_self_dependency_is_a_cycle():
    body = _dep_body(_projection_subs(), ["#1", "#1"])
    assert _dep_ids(body) == [_DEP_CYCLE]


def test_declared_edge_closing_cycle_is_reported():
    body = _dep_body(_projection_subs(), ["#2", "none"])
    assert _dep_ids(body) == [_DEP_CYCLE]


def _plan_row(body: str, num: int) -> str:
    return next(line for line in body.splitlines() if line.startswith(f"| {num} | part {num} |"))


def test_apply_inferred_dependency_fixes_replaces_none():
    from issuesmith.gate_rules.b1_milestone_subdesign import apply_inferred_dependency_fixes

    body = _dep_body(_projection_subs(), ["none", "none"])
    fixed, applied = apply_inferred_dependency_fixes(body)
    assert applied == [_DEP_MISSING]
    assert _plan_row(fixed, 2) == f"| 2 | part 2 | {_REPO} | c | #1 |"
    assert _plan_row(fixed, 1) == _plan_row(body, 1)
    # Only the depends-on cell changed.
    assert fixed.replace("| c | #1 |", "| c | none |") == body
    assert _DEP_MISSING not in _dep_ids(fixed)
    assert apply_inferred_dependency_fixes(fixed) == (fixed, [])


def test_apply_inferred_dependency_fixes_merges_existing_and_empty():
    from issuesmith.gate_rules.b1_milestone_subdesign import apply_inferred_dependency_fixes

    blocks = [*_projection_subs(), _dep_sub(3, [("src/issuesmith/z.py", "modify", "z")])]
    fixed, _ = apply_inferred_dependency_fixes(_dep_body(blocks, ["none", "#3", "none"]))
    assert _plan_row(fixed, 2).endswith("| #1, #3 |")
    assert apply_inferred_dependency_fixes(fixed) == (fixed, [])

    fixed_empty, _ = apply_inferred_dependency_fixes(_dep_body(_projection_subs(), ["none", ""]))
    assert _plan_row(fixed_empty, 2).endswith("| #1 |")


def test_apply_inferred_dependency_fixes_skips_cycle_edges():
    from issuesmith.gate_rules.b1_milestone_subdesign import apply_inferred_dependency_fixes

    body = _dep_body(_cycle_subs(), ["none", "none"])
    assert apply_inferred_dependency_fixes(body) == (body, [])


def test_dependency_vocabulary_comes_from_language_pack(tmp_path, monkeypatch):
    """A non-Japanese pack: new kind, depends-on header and no-deps word are config values."""
    import dataclasses

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        apply_inferred_dependency_fixes,
        extract_sub_blocks,
        infer_sub_dependencies,
    )
    from issuesmith.language import EN

    data = {f.name: getattr(EN, f.name) for f in dataclasses.fields(EN)}
    data.update(
        sub_header_prefix=SUB,
        change_table_columns=[REPOSITORY, FILE_PATH, CHANGE_TYPE, DESCRIPTION],
        sub_plan_columns=["#", "Title", "Repo", "What", "Needs"],
        new_words=["create"],
        no_deps_word="-",
    )
    plain = {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}
    plain["sections"] = dict(EN.sections)
    plain["messages"] = dict(EN.messages)
    pack = tmp_path / "pack.yaml"
    pack.write_text(yaml.safe_dump(plain), encoding="utf-8")
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(yaml.safe_dump({"repo": _REPO, "language_pack": str(pack)}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    try:
        blocks = [
            _dep_sub(1, [("src/issuesmith/projection.py", "create", "c")]),
            _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "use projection")]),
        ]
        body = _dep_body(blocks, ["-", "-"])
        assert "| Needs |" in body
        assert infer_sub_dependencies(extract_sub_blocks(body)) == {1: set(), 2: {1}}
        assert _dep_ids(body) == [_DEP_MISSING]
        fixed, _ = apply_inferred_dependency_fixes(body)
        assert _plan_row(fixed, 2).endswith("| #1 |")
        assert _plan_row(fixed, 1).endswith("| - |")
        # "add" is not a new word in this pack.
        blocks[0] = _dep_sub(1, [("src/issuesmith/projection.py", "add", "c")])
        assert _dep_ids(_dep_body(blocks, ["-", "-"])) == []
    finally:
        reset_config_cache()


# ---------------------------------------------------------------------------
# #4818 — ordering sentence without a depends-on entry
# ---------------------------------------------------------------------------

_ORDER_RULE = "b1_milestone_subdesign.sub_order_without_dependency"


def _order_body(sub2_policy: str, dep: str, *, sub2_ac: str = "Sub2 gamma") -> str:
    """Two-sub body whose Sub2 design policy is ``sub2_policy`` and depends-on is ``dep``."""
    body = _with_test_sub_dependency(
        _milestone_body([
            ("impl one", [("docs/a.md", "Modify")]),
            (sub2_policy, [("docs/b.md", "Modify")]),
        ]),
        dep,
    )
    return body.replace("- [ ] Sub2 gamma", f"- [ ] {sub2_ac}")


def _order_hits(body: str):
    return [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _ORDER_RULE]


def test_sub_order_without_dependency_detected():
    hits = _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "none"))
    assert len(hits) == 1
    assert hits[0].severity == "fail"
    assert hits[0].auto_fixable is False
    assert hits[0].location == f"#### {SUB}2"
    assert "Sub 2" in hits[0].message and "Sub 1" in hits[0].message
    assert "#1" in hits[0].fix_hint


def test_sub_order_with_dependency_passes():
    assert _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "#1")) == []


def test_sub_order_dependency_does_not_match_by_prefix():
    hits = _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "#12"))
    assert len(hits) == 1


def test_sub_order_same_sub_referenced_twice_is_one_violation():
    policy = f"Start after {SUB} 1 is merged; once {SUB}1 lands, wire it up."
    assert len(_order_hits(_order_body(policy, "none"))) == 1


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(f"{SUB} 2 runs after the config change.", id="self-reference"),
        pytest.param(f"Afterwards {SUB} 1 stays as is.", id="afterwards"),
        pytest.param(f"Reuse the helper from {SUB} 1.", id="no-order-word"),
    ],
)
def test_sub_order_not_detected(policy):
    assert _order_hits(_order_body(policy, "none")) == []


def test_sub_order_ignores_acceptance_criteria_and_tables():
    body = _order_body(
        "Plain change.", "none", sub2_ac=f"Works after {SUB} 1 is merged"
    )
    body = body.replace(
        "| `sumipan/nexus` | `docs/b.md` | Modify | Description |",
        f"| `sumipan/nexus` | `docs/b.md` | Modify | after {SUB} 1 |",
    )
    assert _order_hits(body) == []


def test_sub_order_skipped_without_depends_on_column():
    body = _milestone_body([
        ("impl one", [("docs/a.md", "Modify")]),
        (f"Start after {SUB} 1 is merged.", [("docs/b.md", "Modify")]),
    ])
    assert _order_hits(body) == []


def test_sub_order_words_come_from_language_pack(tmp_path, monkeypatch):
    _use_pack(
        tmp_path,
        monkeypatch,
        sub_header_prefix=SUB,
        change_table_columns=[REPOSITORY, FILE_PATH, CHANGE_TYPE, DESCRIPTION],
        order_after_words=["following"],
    )
    assert len(_order_hits(_order_body(f"Start following {SUB} 1.", "none"))) == 1
    assert _order_hits(_order_body(f"Start following {SUB} 1.", "#1")) == []
    assert _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "none")) == []


# ---------------------------------------------------------------------------
# #4912 — glob / deletion sibling test matching (#4793)
# ---------------------------------------------------------------------------


def _milestone_body_rows(
    subs: list[tuple[str, list[tuple[str, str, str]]]],
    *,
    allow_paths: tuple[str, ...] = ("tools/**", "tests/**", "src/**"),
) -> str:
    """Like ``_milestone_body`` but each row is ``(path, change_type, content)``."""
    blocks: list[str] = []
    plan_rows: list[str] = []
    for num, (policy, rows) in enumerate(subs, start=1):
        table = "\n".join(
            f"| `sumipan/nexus` | `{path}` | {change_type} | {content} |"
            for path, change_type, content in rows
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


def _sibling_hits(body: str):
    return [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _SIBLING_TEST_RULE]


def test_behavior_test_in_sibling_glob_deletion_rejects_despite_dependency():
    delete_word = get_config().language.delete_words[0]
    body = _with_test_sub_dependency(
        _milestone_body_rows([
            ("impl", [("tools/corklab/config.py", "Modify", f"drop symbols {delete_word}")]),
            ("tests", [("tests/tools/corklab/test_*.py", "Modify", "update tests")]),
        ]),
        "#1",
    )
    hits = _sibling_hits(body)
    assert len(hits) == 1
    msg = hits[0].message
    assert "tools/corklab/config.py" in msg
    assert "tests/tools/corklab/test_*.py" in msg
    assert "(deletion)" in msg
    assert "depend" not in (hits[0].fix_hint or "").lower()


def test_behavior_test_in_sibling_glob_without_deletion():
    body = _milestone_body_rows([
        ("impl", [("tools/corklab/config.py", "Modify", "refactor only")]),
        ("tests", [("tests/tools/corklab/test_*.py", "Modify", "update tests")]),
    ])
    hits = _sibling_hits(body)
    assert len(hits) == 1
    assert "(deletion)" not in hits[0].message


def test_behavior_test_in_sibling_glob_dependency_exemption_without_deletion():
    body = _with_test_sub_dependency(
        _milestone_body_rows([
            ("impl", [("tools/corklab/config.py", "Modify", "refactor only")]),
            ("tests", [("tests/tools/corklab/test_*.py", "Modify", "update tests")]),
        ]),
        "#1",
    )
    assert _sibling_hits(body) == []


def test_behavior_test_in_sibling_src_layout_glob_mirror():
    body = _milestone_body_rows([
        ("impl", [("src/pkg/gate_rules/foo.py", "Modify", "change")]),
        ("tests", [("tests/gate_rules/test_*.py", "Modify", "update")]),
    ])
    assert len(_sibling_hits(body)) == 1


def test_behavior_test_in_sibling_deletion_prefix_despite_dependency():
    delete_word = get_config().language.delete_words[0]
    body = _with_test_sub_dependency(
        _milestone_body_rows([
            ("impl", [("tools/corklab/labels.py", f"Modify {delete_word}", "drop labels")]),
            ("tests", [("tests/tools/corklab/test_steps_verify.py", "Add", "new test")]),
        ]),
        "#1",
    )
    hits = _sibling_hits(body)
    assert len(hits) == 1
    assert "(deletion)" in hits[0].message


def test_behavior_test_in_sibling_glob_no_false_positive_other_dir():
    body = _milestone_body_rows([
        ("impl", [("src/x/a.py", "Modify", "change")]),
        ("tests", [("tests/y/test_*.py", "Modify", "update")]),
    ])
    assert _sibling_hits(body) == []


def test_behavior_test_in_sibling_deletion_no_false_positive_other_dir():
    delete_word = get_config().language.delete_words[0]
    body = _milestone_body_rows([
        ("impl", [("tools/foo/a.py", f"Modify {delete_word}", "drop")]),
        ("tests", [("tests/tools/bar/test_b.py", "Add", "test")]),
    ])
    assert _sibling_hits(body) == []


def test_promoted_oversized_body_reports_placeholder_design_per_sub():
    """AC (#4915): each promoted sub gets exactly one placeholder_design violation."""
    from issuesmith.gate_rules.b1_milestone_subdesign import PLACEHOLDER_DESIGN_ID
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_oversized_non_milestone_body())
    hits = [v for v in _check(promoted, MILESTONE_LABELS) if v.rule_id == PLACEHOLDER_DESIGN_ID]
    assert len(hits) == 3
    assert all(v.auto_fixable is False for v in hits)
    assert all(v.fix_hint for v in hits)


def test_placeholder_design_cleared_when_one_sub_is_filled_in():
    """AC (#4915): a sub with real design and extra AC no longer reports placeholder."""
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        PLACEHOLDER_AC_TEMPLATES,
        PLACEHOLDER_DESIGN_ID,
    )
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_oversized_non_milestone_body())
    scope, policy, changed, ac = get_config().sub_design_subsections
    concern = "src/a"
    real_ac = [
        PLACEHOLDER_AC_TEMPLATES[0].format(concern=concern, num=1),
        "Run unit tests for module a",
    ]
    ac_lines = "\n".join(f"- [ ] {line}" for line in real_ac)
    replacement = f"""\
#### {SUB}1: {concern}

**{scope}**: Real scope for concern a
**{policy}**: Implement module a with shared helpers
**{changed}**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `src/a/f1.py` | Modify | implement f1 |
| `sumipan/nexus` | `src/a/f2.py` | Modify | implement f2 |
| `sumipan/nexus` | `src/a/f3.py` | Modify | implement f3 |

**{ac}**:
{ac_lines}
"""
    design_hdr = f"## {get_config().sections['design']}"
    design = promoted.split(design_hdr, 1)[1]
    first_sub_end = design.find(f"#### {SUB}2:")
    patched = (
        promoted[: promoted.index(design_hdr) + len(design_hdr)]
        + "\n\n"
        + replacement
        + design[first_sub_end:]
    )
    hits = [v for v in _check(patched, MILESTONE_LABELS) if v.rule_id == PLACEHOLDER_DESIGN_ID]
    assert all("Sub 1" not in v.message for v in hits)
    assert len(hits) == 2


def _minimal_milestone_with_sub(block: str) -> str:
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tools/foo/**
```

## Design

{block}

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | scope1 | None |

## Changed Files
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | add a |

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo/a.py
```
"""


def test_placeholder_design_detects_scope_marker_only():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        PLACEHOLDER_DESIGN_ID,
        PLACEHOLDER_DESIGN_MARKER,
    )

    scope, policy, changed, ac = get_config().sub_design_subsections
    block = f"""\
#### {SUB}1: foo

**{scope}**: Real scope text here
**{policy}**: {PLACEHOLDER_DESIGN_MARKER} `tools/foo`
**{changed}**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | concrete change description |

**{ac}**:
- [ ] First real acceptance criterion alpha
- [ ] Second real acceptance criterion beta
- [ ] Third real acceptance criterion gamma
"""
    hits = [
        v
        for v in _check(_minimal_milestone_with_sub(block), MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert len(hits) == 1
    assert "scope/design policy marker" in hits[0].message


def test_placeholder_design_detects_change_content_only():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        PLACEHOLDER_CHANGE_CONTENT,
        PLACEHOLDER_DESIGN_ID,
    )

    scope, policy, changed, ac = get_config().sub_design_subsections
    block = f"""\
#### {SUB}1: foo

**{scope}**: Real scope text here
**{policy}**: Real design policy without marker prefix
**{changed}**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | {PLACEHOLDER_CHANGE_CONTENT} |

**{ac}**:
- [ ] First real acceptance criterion alpha
- [ ] Second real acceptance criterion beta
- [ ] Third real acceptance criterion gamma
"""
    hits = [
        v
        for v in _check(_minimal_milestone_with_sub(block), MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert len(hits) == 1
    assert "change content" in hits[0].message


def test_placeholder_design_detects_template_ac_only():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        PLACEHOLDER_AC_TEMPLATES,
        PLACEHOLDER_DESIGN_ID,
    )

    scope, policy, changed, ac = get_config().sub_design_subsections
    concern = "tools/foo"
    ac_lines = "\n".join(
        f"- [ ] {tpl.format(concern=concern, num=1)}" for tpl in PLACEHOLDER_AC_TEMPLATES
    )
    block = f"""\
#### {SUB}1: foo

**{scope}**: Real scope text here
**{policy}**: Real design policy without marker prefix
**{changed}**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | concrete change description |

**{ac}**:
{ac_lines}
"""
    hits = [
        v
        for v in _check(_minimal_milestone_with_sub(block), MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert len(hits) == 1
    assert "template-only AC" in hits[0].message


def test_placeholder_design_no_false_positive_marker_mid_sentence():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        PLACEHOLDER_DESIGN_ID,
        PLACEHOLDER_DESIGN_MARKER,
    )

    scope, policy, changed, ac = get_config().sub_design_subsections
    block = f"""\
#### {SUB}1: foo

**{scope}**: Work continues after {PLACEHOLDER_DESIGN_MARKER} `tools/foo` in prose
**{policy}**: Real design policy
**{changed}**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | concrete change description |

**{ac}**:
- [ ] First real acceptance criterion alpha
- [ ] Second real acceptance criterion beta
- [ ] Third real acceptance criterion gamma
"""
    hits = [
        v
        for v in _check(_minimal_milestone_with_sub(block), MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert hits == []


def test_placeholder_design_no_false_positive_mixed_ac():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        PLACEHOLDER_AC_TEMPLATES,
        PLACEHOLDER_DESIGN_ID,
    )

    scope, policy, changed, ac = get_config().sub_design_subsections
    concern = "tools/foo"
    block = f"""\
#### {SUB}1: foo

**{scope}**: Real scope text here
**{policy}**: Real design policy without marker prefix
**{changed}**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | concrete change description |

**{ac}**:
- [ ] {PLACEHOLDER_AC_TEMPLATES[0].format(concern=concern, num=1)}
- [ ] Real acceptance criterion beyond templates
- [ ] Another real acceptance criterion item
"""
    hits = [
        v
        for v in _check(_minimal_milestone_with_sub(block), MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert hits == []


def test_placeholder_design_skipped_without_milestone_label():
    from issuesmith.gate_rules.b1_milestone_subdesign import PLACEHOLDER_DESIGN_ID
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_oversized_non_milestone_body())
    hits = [
        v
        for v in _check(promoted, NON_MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert hits == []


def test_sub_plan_dep_format_fixes_cases():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        _SUB_PLAN_DEP_FORMAT_ID,
        apply_sub_plan_dep_format_fixes,
    )
    from tests.legacy_text import NONE

    def _plan_body(dep: str) -> str:
        return _dep_body(_projection_subs(), ["none", dep])

    cases = [
        ("2", "#2"),
        ("4, 2", "#4, #2"),
        ("#2", "#2"),
        (NONE, NONE),
        ("サブ2のマージ後", "サブ2のマージ後"),
    ]
    for dep, expected in cases:
        body = _plan_body(dep)
        fixed, applied = apply_sub_plan_dep_format_fixes(body)
        if dep in ("#2", NONE, "サブ2のマージ後"):
            assert applied == []
            assert dep in fixed
        else:
            assert applied == [_SUB_PLAN_DEP_FORMAT_ID]
            assert f"| 2 | part 2 | {_REPO} | c | {expected} |" in fixed
    once, first_applied = apply_sub_plan_dep_format_fixes(_plan_body("2"))
    assert first_applied == [_SUB_PLAN_DEP_FORMAT_ID]
    _, second_applied = apply_sub_plan_dep_format_fixes(once)
    assert second_applied == []


def test_sub_plan_dep_format_violation_auto_fixable():
    from issuesmith.gate_rules.b1_milestone_subdesign import _SUB_PLAN_DEP_FORMAT_ID

    bare = _dep_body(_projection_subs(), ["none", "2"])
    hits = [v for v in _check(bare, MILESTONE_LABELS) if v.rule_id == _SUB_PLAN_DEP_FORMAT_ID]
    assert len(hits) == 1 and hits[0].auto_fixable
    fixed = _dep_body(_projection_subs(), ["none", "#2"])
    assert not [v for v in _check(fixed, MILESTONE_LABELS) if v.rule_id == _SUB_PLAN_DEP_FORMAT_ID]
