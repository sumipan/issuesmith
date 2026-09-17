"""test_b1_tier.py — unit tests for B1 brushup tier selection"""

from issuesmith.b1_tier import determine_b1_tier


def test_design_doc_with_background_and_design_returns_heavy():
    # Japanese text intentionally kept for CJK processing test
    body = """## 背景・目的

Purpose description

## 設計

Implementation approach
"""
    assert determine_b1_tier(body) == "heavy"


def test_design_doc_with_design_only_returns_heavy():
    # Japanese text intentionally kept for CJK processing test
    body = """## 設計

Module split
"""
    assert determine_b1_tier(body) == "heavy"


def test_light_fix_ac_only_returns_light():
    # Japanese text intentionally kept for CJK processing test
    body = """## 受け入れ条件

- [ ] Tests pass

## やらないこと

- Out-of-scope changes
"""
    assert determine_b1_tier(body) == "light"


def test_background_without_design_returns_heavy():
    # Japanese text intentionally kept for CJK processing test
    body = """## 背景・目的

Background only
"""
    assert determine_b1_tier(body) == "heavy"


def test_empty_body_returns_light():
    assert determine_b1_tier("") == "light"


def test_boundary_ac_section_alone_not_design_doc():
    # Japanese text intentionally kept for CJK processing test
    body = "## 受け入れ条件\n- [ ] item\n"
    assert determine_b1_tier(body) == "light"
