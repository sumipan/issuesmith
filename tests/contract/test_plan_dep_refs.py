"""contract.plan_dep_refs — shared split-plan dependency tokens (#4929)."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from issuesmith.config import get_config
from issuesmith.contract import plan_dep_refs
from issuesmith.gate_rules.b1_milestone_subdesign import _sub_plan_dependencies
from issuesmith.milestone import Sub1State, resolve_dependencies


def test_plan_dep_refs_dedupes_and_preserves_order() -> None:
    assert plan_dep_refs("#4, 2, #4") == [4, 2]


def test_plan_dep_refs_none_and_empty() -> None:
    assert plan_dep_refs(get_config().language.no_deps_word) == []
    assert plan_dep_refs("") == []


def test_sub_plan_dependencies_bare_and_hash_equivalent() -> None:
    from tests.gate_rules.test_b1_milestone_subdesign import _dep_body, _projection_subs

    bare = _dep_body(_projection_subs(), ["none", "2, 4"])
    hashed = _dep_body(_projection_subs(), ["none", "#2, #4"])
    assert _sub_plan_dependencies(bare)[2] == {2, 4}
    assert _sub_plan_dependencies(hashed)[2] == {2, 4}


def test_shared_tokenizer_required_for_bare_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    strict = re.compile(r"#(\d+)")
    monkeypatch.setattr("issuesmith.contract.PLAN_DEP_REF_RE", strict)
    monkeypatch.setattr("issuesmith.milestone.PLAN_DEP_REF_RE", strict)
    assert plan_dep_refs("2") == []
    state = Sub1State()
    client = MagicMock()
    out = resolve_dependencies(
        "2",
        table_row_count=6,
        row_to_issue={2: 4922},
        client=client,
        state=state,
    )
    assert out == "2"
    body = (
        "## Milestone\n\n### Sub-issue Plan\n"
        "| # | Title | Content | Dependency |\n"
        "|---|--------|------|------|\n"
        "| 2 | x | y | 2 |\n"
    )
    assert _sub_plan_dependencies(body).get(2) != {2}
