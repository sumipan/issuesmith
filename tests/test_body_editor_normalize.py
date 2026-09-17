"""tests/test_body_editor_normalize.py — normalize_sub_headers / relocate_sub_plan."""
from __future__ import annotations

from issuesmith.body_editor import normalize_sub_headers, relocate_sub_plan


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
    # Japanese text intentionally kept for CJK processing test
    body = """\
## 設計

intro

### サブイシュー分割計画
| # | Title |
|---|--------|
| 1 | a |

### Other heading
keep

## やらないこと

out
"""
    out = relocate_sub_plan(body)
    assert "### サブイシュー分割計画" in out
    design_idx = out.index("## 設計")
    milestone_idx = out.index("## マイルストーン")
    plan_idx = out.index("### サブイシュー分割計画")
    other_idx = out.index("### Other heading")
    assert design_idx < other_idx < milestone_idx < plan_idx
    # removed from design: only one occurrence of the plan heading
    assert out.count("### サブイシュー分割計画") == 1
    assert "| 1 | a |" in out[plan_idx:]


def test_relocate_sub_plan_creates_milestone_before_out_of_scope():
    # Japanese text intentionally kept for CJK processing test
    body = """\
## 設計

### サブイシュー分割計画
| # | t |
|---|---|
| 1 | x |

## やらないこと

y
"""
    out = relocate_sub_plan(body)
    assert out.index("## マイルストーン") < out.index("## やらないこと")


def test_relocate_sub_plan_noop_when_already_under_milestone():
    # Japanese text intentionally kept for CJK processing test
    body = """\
## 設計

#### サブ1: a

## マイルストーン

### サブイシュー分割計画
| # | t |
|---|---|
| 1 | x |
"""
    assert relocate_sub_plan(body) == body


def test_relocate_sub_plan_noop_when_no_plan_in_design():
    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\nno plan\n\n## マイルストーン\nok\n"
    assert relocate_sub_plan(body) == body
