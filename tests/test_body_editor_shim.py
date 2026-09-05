"""test_body_editor_shim.py — issuesmith.body_editor 互換シムの import テスト。

brushup.md / sub-ready.md の order テンプレートが import する
`issuesmith.body_editor` が実体（ghdag.markdown.body_editor）へ解決できることを保証する。
"""
from __future__ import annotations


def test_shim_exports_are_callable():
    from issuesmith.body_editor import (
        count_heading,
        filter_section_by_paths,
        get_section,
        get_subsections,
        upsert_section,
    )

    body = "## 設計\n\n本文\n"
    assert count_heading(body, "設計") == 1
    assert get_section(body, "設計").strip() == "本文"
    assert callable(upsert_section)
    assert callable(get_subsections)
    assert callable(filter_section_by_paths)


def test_get_section_by_keyword_matches_suffixed_heading():
    """`## 受け入れ条件（Phase 1）` のようなサフィックス付き見出しを拾う（#2535 回帰）"""
    from issuesmith.body_editor import get_section, get_section_by_keyword

    body = "## 設計\n\n設計本文\n\n## 受け入れ条件（Phase 1）\n\n- [x] AC1\n"
    assert get_section(body, "受け入れ条件") is None
    result = get_section_by_keyword(body, "受け入れ条件")
    assert result is not None and "AC1" in result


def test_get_section_by_keyword_returns_none_when_absent():
    from issuesmith.body_editor import get_section_by_keyword

    assert get_section_by_keyword("## 設計\n\n本文\n", "受け入れ条件") is None
