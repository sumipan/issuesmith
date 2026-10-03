"""sub_header_re() / lazy SUB_HEADER_RE and change-table columns follow the language pack (#4469)."""

from __future__ import annotations

import dataclasses

import pytest

import issuesmith.config as config_module
from issuesmith.contract import (
    SUB_HEADER_RE,
    extract_change_table_rows,
    iter_sub_blocks,
    sub_block,
    sub_header_re,
)
from issuesmith.language import EN


@pytest.fixture
def use_pack(monkeypatch):
    def _use(**overrides):
        monkeypatch.setattr(config_module, "EN", dataclasses.replace(EN, **overrides))
        config_module.reset_config_cache()

    return _use


def test_sub_header_re_uses_pack_prefix(use_pack):
    use_pack(sub_header_prefix="Part")
    text = "#### Part1: a\nbody\n#### Part 2: b\n#### Sub3: c\n"
    assert sub_header_re().findall(text) == ["1", "2"]
    assert [n for n, _ in iter_sub_blocks(text)] == [1, 2]
    assert sub_block(text, 1) == "#### Part1: a\nbody\n"


def test_sub_header_re_escapes_prefix(use_pack):
    use_pack(sub_header_prefix="S.")
    assert sub_header_re().findall("#### S.1: a\n#### SX2: b\n") == ["1"]


def test_sub_header_re_requires_h4_and_colon(use_pack):
    use_pack(sub_header_prefix="Sub")
    assert sub_header_re().findall("### Sub1: a\n#### Sub2 b\n#### Sub3: c\n") == ["3"]


def test_lazy_object_delegates_to_current_pack(use_pack):
    use_pack(sub_header_prefix="Sub")
    assert SUB_HEADER_RE.findall("#### Sub7: x\n") == ["7"]
    use_pack(sub_header_prefix="Part")
    assert SUB_HEADER_RE.findall("#### Sub7: x\n") == []
    assert SUB_HEADER_RE.findall("#### Part7: x\n") == ["7"]
    assert SUB_HEADER_RE.flags == sub_header_re().flags


def test_change_table_columns_from_pack(use_pack):
    use_pack(change_table_columns=("Repository", "File path", "Change type", "Description"))
    table = (
        "| Repository | File Path | Change Type | Description |\n|---|---|---|---|\n"
        "| `o/r` | `src/a.py` | modify | x |\n"
    )
    assert extract_change_table_rows(table) == [("o/r", "src/a.py", "modify")]

    use_pack(change_table_columns=("Repo", "Path", "Kind", "Note"))
    custom = "| Repo | Path | Kind | Note |\n|---|---|---|---|\n| `o/r` | `src/a.py` | modify | x |\n"
    assert extract_change_table_rows(custom) == [("o/r", "src/a.py", "modify")]
