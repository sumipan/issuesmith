"""tests/test_body_editor_normalize.py — normalize_sub_headers / relocate_sub_plan."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from issuesmith.body_editor import normalize_sub_headers, relocate_sub_plan

_ENGLISH_SECTIONS = {
    "design": "Design",
    "milestone": "Milestone",
    "sub_plan": "Sub-issue Split Plan",
}


def _mock_cfg():
    cfg = MagicMock()
    cfg.sections = _ENGLISH_SECTIONS
    return cfg


def test_normalize_sub_headers_title_case():
    body = "#### Sub 1: Title\nrest\n"
    # Japanese text intentionally kept for CJK processing test
    assert normalize_sub_headers(body) == "#### サブ1: Title\nrest\n"


def test_normalize_sub_headers_lower_without_colon():
    body = "#### sub 2\n"
    # Japanese text intentionally kept for CJK processing test
    assert normalize_sub_headers(body) == "#### サブ2:\n"


def test_normalize_sub_headers_upper():
    body = "#### SUB 3: X\n"
    # Japanese text intentionally kept for CJK processing test
    assert normalize_sub_headers(body) == "#### サブ3: X\n"


def test_normalize_sub_headers_noop_for_japanese():
    # Japanese text intentionally kept for CJK processing test
    body = "#### サブ1: existing\n"
    assert normalize_sub_headers(body) == body


def test_relocate_sub_plan_moves_from_design_to_milestone():
    body = """\
## Design

intro

### Sub-issue Split Plan
| # | Title |
|---|--------|
| 1 | a |

### Other heading
keep

## Out of scope

out
"""
    with patch("issuesmith.body_editor.get_config", return_value=_mock_cfg()):
        out = relocate_sub_plan(body)
    assert "### Sub-issue Split Plan" in out
    design_idx = out.index("## Design")
    milestone_idx = out.index("## Milestone")
    plan_idx = out.index("### Sub-issue Split Plan")
    other_idx = out.index("### Other heading")
    assert design_idx < other_idx < milestone_idx < plan_idx
    assert out.count("### Sub-issue Split Plan") == 1
    assert "| 1 | a |" in out[plan_idx:]


def test_relocate_sub_plan_creates_milestone_when_none_exists():
    body = """\
## Design

### Sub-issue Split Plan
| # | t |
|---|---|
| 1 | x |
"""
    with patch("issuesmith.body_editor.get_config", return_value=_mock_cfg()):
        out = relocate_sub_plan(body)
    assert "## Milestone" in out
    assert "### Sub-issue Split Plan" in out
    assert out.rindex("## Design") < out.rindex("## Milestone")


def test_relocate_sub_plan_noop_when_already_under_milestone():
    body = """\
## Design

#### Sub 1: a

## Milestone

### Sub-issue Split Plan
| # | t |
|---|---|
| 1 | x |
"""
    with patch("issuesmith.body_editor.get_config", return_value=_mock_cfg()):
        assert relocate_sub_plan(body) == body


def test_relocate_sub_plan_noop_when_no_plan_in_design():
    body = "## Design\nno plan\n\n## Milestone\nok\n"
    with patch("issuesmith.body_editor.get_config", return_value=_mock_cfg()):
        assert relocate_sub_plan(body) == body
