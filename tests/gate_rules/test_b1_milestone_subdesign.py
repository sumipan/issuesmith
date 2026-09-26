"""tests/gate_rules/test_b1_milestone_subdesign.py — unit tests for b1_milestone_subdesign gate."""
from __future__ import annotations

import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: F401
from issuesmith.gate_rules import GATE_REGISTRY
from tests.legacy_text import CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY, SUB, VAGUE_SUCCESS

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
- [ ] {VAGUE_SUCCESS} result
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
    monkeypatch.setattr(sys, "argv", ["b1_verify", "4048", "--prev-report", str(prev)])
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
    monkeypatch.setattr(sys, "argv", ["b1_verify", "4048"])
    assert b1_verify.main() == 1
    assert "oscillation" not in capsys.readouterr().out
