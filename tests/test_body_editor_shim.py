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

    body = "## Design\n\nbody text\n"
    assert count_heading(body, "Design") == 1
    assert get_section(body, "Design").strip() == "body text"
    assert callable(upsert_section)
    assert callable(get_subsections)
    assert callable(filter_section_by_paths)


def test_get_section_by_keyword_matches_suffixed_heading():
    """Pick up suffixed headings like acceptance-criteria with a Phase suffix (#2535 regression)"""
    from issuesmith.body_editor import get_section, get_section_by_keyword

    body = "## Design\n\ndesign body\n\n## Acceptance Criteria (Phase 1)\n\n- [x] AC1\n"
    assert get_section(body, "Acceptance Criteria") is None
    result = get_section_by_keyword(body, "Acceptance Criteria")
    assert result is not None and "AC1" in result


def test_get_section_by_keyword_returns_none_when_absent():
    from issuesmith.body_editor import get_section_by_keyword

    assert get_section_by_keyword("## Design\n\nbody text\n", "Acceptance Criteria") is None
