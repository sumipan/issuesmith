"""Structural completeness tests for GATE_REGISTRY (#3671).

AC-2: verifies that:
  (a) gate_rules/*.py ids in the rule-class registry are in the unified GATE_REGISTRY
  (b) gates/worktree.py *Gate classes are all covered in GATE_REGISTRY
  (c) deps / scope / pr_scope / m2 are in GATE_REGISTRY
"""
from __future__ import annotations

import pytest

import issuesmith.gate_rules  # noqa: F401 — register gate_rules side effects
from issuesmith.gate_registry import GATE_REGISTRY as _RULE_REG
from issuesmith.gates import GATE_REGISTRY

# ---------------------------------------------------------------------------
# (a) All rule-class gate_rules ids are in GATE_REGISTRY
# ---------------------------------------------------------------------------


def test_all_gate_rule_ids_in_registry() -> None:
    missing = [gid for gid in _RULE_REG if gid not in GATE_REGISTRY]
    assert not missing, (
        f"gate_rules ids registered in rule registry but missing from GATE_REGISTRY: {missing}"
    )
    assert GATE_REGISTRY["ac_format"].input_kind == "issue"


# ---------------------------------------------------------------------------
# (b) All *Gate classes in gates/worktree.py are covered
# ---------------------------------------------------------------------------


def test_all_worktree_gate_classes_in_registry() -> None:
    from issuesmith.gates.worktree import WORKTREE_GATES

    missing = [gid for gid in WORKTREE_GATES if gid not in GATE_REGISTRY]
    assert not missing, (
        f"WORKTREE_GATES keys missing from GATE_REGISTRY: {missing}"
    )


# ---------------------------------------------------------------------------
# (c) Core gate ids are present
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate_id", ["deps", "scope", "pr_scope", "ac_complete"])
def test_core_gate_ids_present(gate_id: str) -> None:
    assert gate_id in GATE_REGISTRY, f"core gate {gate_id!r} missing from GATE_REGISTRY"
