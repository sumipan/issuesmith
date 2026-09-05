"""test_b1_tier.py — B1 brushup tier のユニットテスト"""

from issuesmith.b1_tier import determine_b1_tier


def test_design_doc_with_background_and_design_returns_heavy():
    body = """## 背景・目的

目的の説明

## 設計

実装方針
"""
    assert determine_b1_tier(body) == "heavy"


def test_design_doc_with_design_only_returns_heavy():
    body = """## 設計

モジュール分割
"""
    assert determine_b1_tier(body) == "heavy"


def test_light_fix_ac_only_returns_light():
    body = """## 受け入れ条件

- [ ] テストが通ること

## やらないこと

- スコープ外の変更
"""
    assert determine_b1_tier(body) == "light"


def test_background_without_design_returns_heavy():
    body = """## 背景・目的

のみ記載
"""
    assert determine_b1_tier(body) == "heavy"


def test_empty_body_returns_light():
    assert determine_b1_tier("") == "light"


def test_boundary_ac_section_alone_not_design_doc():
    body = "## 受け入れ条件\n- [ ] item\n"
    assert determine_b1_tier(body) == "light"
