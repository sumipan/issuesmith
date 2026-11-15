"""sub_header_re() / lazy SUB_HEADER_RE and change-table columns follow the language pack (#4469)."""

from __future__ import annotations

import dataclasses

import pytest

import issuesmith.config as config_module
from issuesmith.contract import iter_sub_blocks, sub_block, sub_header_re
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


