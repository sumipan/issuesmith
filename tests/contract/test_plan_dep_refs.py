"""contract.plan_dep_refs — shared split-plan dependency tokens (#4929)."""

from __future__ import annotations

from issuesmith.config import get_config
from issuesmith.contract import plan_dep_refs


def test_plan_dep_refs_dedupes_and_preserves_order() -> None:
    assert plan_dep_refs("#4, 2, #4") == [4, 2]


def test_plan_dep_refs_none_and_empty() -> None:
    assert plan_dep_refs(get_config().language.no_deps_word) == []
    assert plan_dep_refs("") == []
