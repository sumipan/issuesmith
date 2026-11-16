"""b1_milestone_subdesign: placeholder designs, structured sub blocks/AC and fix-report oscillation."""

from __future__ import annotations

import issuesmith.gate_rules.milestone_subdesign_rules  # noqa: F401
from issuesmith.config import get_config
from tests.gate_rules.test_milestone_subdesign import (
    _TABLE_HEADER,
    MILESTONE_LABELS,
    _check,
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




def test_iter_sub_blocks_empty_when_no_subs():
    """iter_sub_blocks: returns [] when the input contains no sub headers."""
    from issuesmith.contract import iter_sub_blocks

    assert iter_sub_blocks("") == []
    assert iter_sub_blocks("## Design\nno subs here\n") == []


def test_sub_block_returns_empty_when_no_subs():
    """sub_block: returns '' when no sub headers exist."""
    from issuesmith.contract import sub_block

    assert sub_block("## Design\nno subs\n", 1) == ""




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






