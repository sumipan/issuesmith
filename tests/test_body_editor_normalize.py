"""tests/test_body_editor_normalize.py — normalize_sub_headers / relocate_sub_plan."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

from issuesmith.body_editor import normalize_sub_headers, relocate_sub_plan
from issuesmith.language import EN

_ENGLISH_SECTIONS = {
    "design": "Design",
    "milestone": "Milestone",
    "sub_plan": "Sub-issue Split Plan",
}


def _mock_cfg():
    cfg = MagicMock()
    cfg.sections = _ENGLISH_SECTIONS
    cfg.language = EN
    return cfg


def test_normalize_sub_headers_title_case():
    body = "#### Sub 1: Title\nrest\n"
    out = normalize_sub_headers(body)
    assert out.endswith("1: Title\nrest\n")
    assert "Sub 1" not in out


def test_normalize_sub_headers_lower_without_colon():
    body = "#### sub 2\n"
    out = normalize_sub_headers(body)
    assert out.endswith("2:\n")
    assert "sub 2" not in out


def test_normalize_sub_headers_upper():
    body = "#### SUB 3: X\n"
    out = normalize_sub_headers(body)
    assert out.endswith("3: X\n")
    assert "SUB 3" not in out


def test_normalize_sub_headers_noop_for_canonical_form():
    # ASCII fixture data.
    body = "#### Sub1: existing\n"
    assert normalize_sub_headers(body) == body


def _with_prefix(prefix: str):
    cfg = MagicMock()
    cfg.language = dataclasses.replace(EN, sub_header_prefix=prefix)
    return patch("issuesmith.body_editor.get_config", return_value=cfg)


def test_normalize_sub_headers_uses_pack_prefix():
    with _with_prefix("Part"):
        assert normalize_sub_headers("#### Sub 2: x\n#### sub 3\n") == "#### Part2: x\n#### Part3:\n"


def test_normalize_sub_headers_en_prefix_is_idempotent():
    with _with_prefix("Sub"):
        once = normalize_sub_headers("#### Sub 4: x\n")
        assert once == "#### Sub4: x\n"
        assert normalize_sub_headers(once) == once


def test_normalize_sub_headers_prefix_is_literal():
    with _with_prefix(r"P\1"):
        assert normalize_sub_headers("#### Sub 5: x\n") == "#### P\\15: x\n"


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


def test_relocate_sub_plan_inserts_before_pack_out_of_scope_heading():
    body = (
        "## Design\n\n### Sub-issue Split Plan\n| # | t |\n|---|---|\n| 1 | x |\n\n"
        f"## {EN.out_of_scope_heading}\n\nnope\n\n## Notes\n\nlast\n"
    )
    with patch("issuesmith.body_editor.get_config", return_value=_mock_cfg()):
        out = relocate_sub_plan(body)
    assert out.index("## Milestone") < out.index(f"## {EN.out_of_scope_heading}") < out.index("## Notes")
