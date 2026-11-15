"""Inline code spans in markdown table rows (#6247, sumipan/nexus)."""

from __future__ import annotations

from issuesmith.contract import _split_table_row, table_cell_spans

_ROW_A = "| 1 | `` ` ```ghdag ` `` block | sumipan/ghdag | `a.py` | none |"
_ROW_B = "| ``a`|b`` | c | `d` |"
_ROW_C = "| ``` x | `y` | z |"

_EXPECT_A = ["1", "`` ` ```ghdag ` `` block", "sumipan/ghdag", "`a.py`", "none"]
_EXPECT_B = ["``a`|b``", "c", "`d`"]
_EXPECT_C = ["``` x", "`y`", "z"]


def test_split_table_row_multibacktick_title_cell() -> None:
    """Refs sumipan/nexus#6247: length-2 span around nested backtick runs."""
    assert _split_table_row(_ROW_A) == _EXPECT_A


def test_split_table_row_pipe_inside_code_span() -> None:
    """Refs sumipan/nexus#6247: pipe inside a length-2 code span stays in one cell."""
    assert _split_table_row(_ROW_B) == _EXPECT_B


def test_split_table_row_unclosed_backtick_run_is_literal() -> None:
    """Refs sumipan/nexus#6247: unclosed ``` does not swallow following delimiters."""
    assert _split_table_row(_ROW_C) == _EXPECT_C


def test_table_cell_spans_match_split_table_row() -> None:
    """Refs sumipan/nexus#6247: dependency cell rewrites use the same split as parse."""
    line = _ROW_A
    cells = _split_table_row(line)
    spans = table_cell_spans(line)
    assert len(spans) == 5
    assert [line[s:e].strip() for s, e in spans] == cells
