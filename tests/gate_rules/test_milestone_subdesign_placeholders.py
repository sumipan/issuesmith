"""b1_milestone_subdesign: placeholder designs, structured sub blocks/AC and fix-report oscillation."""

from __future__ import annotations

import issuesmith.gate_rules.milestone_subdesign_rules  # noqa: F401
from issuesmith.config import get_config
from tests.gate_rules.test_milestone_subdesign import (
    _TABLE_HEADER,
    MILESTONE_LABELS,
    NON_MILESTONE_LABELS,
    _check,
    _oversized_non_milestone_body,
    _sub_block,
)
from tests.legacy_text import SUB

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
    dup_violations = [v for v in violations if v.rule_id == "milestone_subdesign.file_union_duplicate"]
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
    from issuesmith.gate_rules.milestone_subdesign_rules import extract_sub_blocks

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


# --- step_verify oscillation detection (nexus #4076) ---


def _violation(rule_id: str, severity: str = "fail"):
    from issuesmith.gate_registry import Violation

    return Violation(
        rule_id=rule_id,
        severity=severity,
        message="m",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )


_PREV_REPORT = (
    "VERIFY_FAILED_CHECKS: milestone_subdesign.repo_mismatch"
    " milestone_subdesign.repo_mismatch scope_breadth.too_large\n"
    "\n## milestone_subdesign.repo_mismatch\nmsg\n"
)


def test_parse_prev_counts_reads_the_report_header():
    from issuesmith.step_verify import _parse_prev_counts

    counts = _parse_prev_counts(_PREV_REPORT)
    assert counts == {
        "milestone_subdesign.repo_mismatch": 2,
        "scope_breadth.too_large": 1,
    }
    assert _parse_prev_counts("VERIFY_FAILED_CHECKS: (none)\n") == {}
    assert _parse_prev_counts("") == {}


def test_oscillation_detected_when_a_rule_count_grows():
    from issuesmith.step_verify import detect_oscillation

    current = [_violation("milestone_subdesign.repo_mismatch")] * 3
    v = detect_oscillation(current, _PREV_REPORT)
    assert v is not None
    assert v.rule_id == "step_verify.oscillation_detected"
    assert v.severity == "fail"
    assert "milestone_subdesign.repo_mismatch" in v.message


def test_no_oscillation_when_counts_shrink_or_stay():
    from issuesmith.step_verify import detect_oscillation

    current = [_violation("milestone_subdesign.repo_mismatch")] * 2
    assert detect_oscillation(current, _PREV_REPORT) is None
    assert detect_oscillation([], _PREV_REPORT) is None


def test_main_prev_report_appends_oscillation(tmp_path, monkeypatch, capsys):
    import types

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.step_verify import main as step_verify_main

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/app", "steps": {"x": {"verify": "design_doc"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    prev = tmp_path / "prev.txt"
    prev.write_text(_PREV_REPORT, encoding="utf-8")
    forge = types.SimpleNamespace(
        issue_get=lambda *_a, **_k: {"body": "x", "labels": [{"name": "scope:milestone"}]}
    )
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: forge)
    monkeypatch.setattr(
        "issuesmith.step_verify.collect_violations",
        lambda body, labels, **kwargs: [_violation("milestone_subdesign.repo_mismatch")] * 5,
    )
    rc = step_verify_main(
        "x",
        ["4048", "--no-apply-deterministic", "--prev-report", str(prev)],
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "step_verify.oscillation_detected" in out.splitlines()[0]


def test_main_without_prev_report_has_no_oscillation(tmp_path, monkeypatch, capsys):
    import types

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.step_verify import main as step_verify_main

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/app", "steps": {"x": {"verify": "design_doc"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    forge = types.SimpleNamespace(issue_get=lambda *_a, **_k: {"body": "x", "labels": []})
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: forge)
    monkeypatch.setattr(
        "issuesmith.step_verify.collect_violations",
        lambda body, labels, **kwargs: [_violation("milestone_subdesign.repo_mismatch")],
    )
    rc = step_verify_main("x", ["4048", "--no-apply-deterministic"])
    assert rc == 1
    assert "oscillation" not in capsys.readouterr().out


def test_promoted_oversized_body_reports_placeholder_design_per_sub():
    """AC (#4915): each promoted sub gets exactly one placeholder_design violation."""
    from issuesmith.gate_rules.milestone_subdesign_rules import PLACEHOLDER_DESIGN_ID
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_oversized_non_milestone_body())
    hits = [v for v in _check(promoted, MILESTONE_LABELS) if v.rule_id == PLACEHOLDER_DESIGN_ID]
    assert len(hits) == 3
    assert all(v.auto_fixable is False for v in hits)
    assert all(v.fix_hint for v in hits)


def test_placeholder_design_cleared_when_one_sub_is_filled_in():
    """AC (#4915): a sub with real design and extra AC no longer reports placeholder."""
    from issuesmith.gate_rules.milestone_subdesign_rules import (
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
    from issuesmith.gate_rules.milestone_subdesign_rules import (
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
    from issuesmith.gate_rules.milestone_subdesign_rules import (
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
    from issuesmith.gate_rules.milestone_subdesign_rules import (
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
    from issuesmith.gate_rules.milestone_subdesign_rules import (
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
    from issuesmith.gate_rules.milestone_subdesign_rules import (
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
    from issuesmith.gate_rules.milestone_subdesign_rules import PLACEHOLDER_DESIGN_ID
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_oversized_non_milestone_body())
    hits = [
        v
        for v in _check(promoted, NON_MILESTONE_LABELS)
        if v.rule_id == PLACEHOLDER_DESIGN_ID
    ]
    assert hits == []


def test_placeholder_design_fix_hint_allows_ac_key_moves():
    from issuesmith.gate_rules.milestone_subdesign_rules import (
        PLACEHOLDER_DESIGN_ID,
        PLACEHOLDER_DESIGN_MARKER,
    )

    promoted_body = _minimal_milestone_with_sub(
        f"""\
#### {SUB}1: foo

**Scope**: {PLACEHOLDER_DESIGN_MARKER} `foo`
**Design Policy**: Real policy
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/foo/a.py` | Add | add |

**Acceptance Criteria**:
- [ ] Concern `foo` change table lists every assigned path
- [ ] Sub design `1` includes the required subsections
- [ ] Parent change-file union includes every path under `foo`
"""
    )
    hits = [v for v in _check(promoted_body, MILESTONE_LABELS) if v.rule_id == PLACEHOLDER_DESIGN_ID]
    assert hits
    hint = hits[0].fix_hint or ""
    assert "paths_must_exist" in hint
    assert "paths_must_not_exist" in hint
    assert "or YAML blocks" not in hint
