"""test_body_editor_shim.py — import tests for issuesmith.body_editor compat shim.

Guarantees that `issuesmith.body_editor`, imported by brushup.md / sub-ready.md
order templates, resolves to the real implementation (ghdag.markdown.body_editor).
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

    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\n\nbody text\n"
    assert count_heading(body, "設計") == 1
    assert get_section(body, "設計").strip() == "body text"
    assert callable(upsert_section)
    assert callable(get_subsections)
    assert callable(filter_section_by_paths)


def test_get_section_by_keyword_matches_suffixed_heading():
    """Pick up suffixed headings like acceptance-criteria with a Phase suffix (#2535 regression)"""
    from issuesmith.body_editor import get_section, get_section_by_keyword

    # Japanese text intentionally kept for CJK processing test
    body = "## 設計\n\ndesign body\n\n## 受け入れ条件（Phase 1）\n\n- [x] AC1\n"
    assert get_section(body, "受け入れ条件") is None
    result = get_section_by_keyword(body, "受け入れ条件")
    assert result is not None and "AC1" in result


def test_get_section_by_keyword_returns_none_when_absent():
    from issuesmith.body_editor import get_section_by_keyword

    # Japanese text intentionally kept for CJK processing test
    assert get_section_by_keyword("## 設計\n\nbody text\n", "受け入れ条件") is None
