"""Regression tests for issuesmith.body_editor H2 section helpers (#6349)."""

from __future__ import annotations

import pytest

from issuesmith.body_editor import (
    count_heading,
    filter_section_by_paths,
    get_section,
    get_section_by_keyword,
    get_subsections,
    split_h2_sections,
    upsert_section,
)


def test_split_h2_sections() -> None:
    body = "Preamble\n## Design\nfoo\n### Subheading\nbar\n## Next\nbaz\n"
    assert split_h2_sections(body) == [
        ("## Design", "## Design\nfoo\n### Subheading\nbar\n"),
        ("## Next", "## Next\nbaz\n"),
    ]


def test_count_heading() -> None:
    body = "## Design\nfoo\n## Design\nbar"
    assert count_heading(body, "Design") == 2


def test_get_section_duplicate_heading_raises() -> None:
    body = "## Design\nfoo\n## Design\nbar"
    with pytest.raises(ValueError, match=r"Duplicate heading: '## Design' appears 2 times"):
        get_section(body, "Design")


def test_upsert_section() -> None:
    body = "## Design\nold\n## Next"
    assert upsert_section(body, "Design", "new") == "## Design\nnew\n## Next"


def test_filter_section_by_paths() -> None:
    section = "- src/foo.py\n- src/bar.py\n"
    assert filter_section_by_paths(section, ["src/foo.py"]) == ["- src/foo.py"]


def test_get_subsections() -> None:
    body = "## Design\n#### Sub#1: X\nfoo\n#### Sub#2: Y\nbar\n## Next"
    assert get_subsections(body, "Design", "#### Sub#") == [
        ("#### Sub#1: X", "foo\n"),
        ("#### Sub#2: Y", "bar\n"),
    ]


def test_get_section_by_keyword_matches_suffixed_heading() -> None:
    """Pick up suffixed headings like acceptance-criteria with a Phase suffix (#2535)."""
    body = "## Design\n\ndesign body\n\n## Acceptance Criteria (Phase 1)\n\n- [x] AC1\n"
    assert get_section(body, "Acceptance Criteria") is None
    result = get_section_by_keyword(body, "Acceptance Criteria")
    assert result is not None and "AC1" in result


def test_get_section_by_keyword_returns_none_for_missing_keyword() -> None:
    assert get_section_by_keyword("## Design\n\nbody text\n", "Acceptance Criteria") is None
