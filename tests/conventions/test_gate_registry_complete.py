"""Structural completeness tests for GATE_REGISTRY (#3671).

AC-2: verifies that:
  (a) gate_rules/*.py ids registered in ghdag's GATE_REGISTRY are in issuesmith's GATE_REGISTRY
  (b) gates/worktree.py *Gate classes are all covered in GATE_REGISTRY
  (c) deps / scope / pr_scope / m2 are in GATE_REGISTRY
  (d) README.md gate table ids match GATE_REGISTRY keys exactly
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from ghdag.workflow.gates import GATE_REGISTRY as _GHDAG_REG

import issuesmith.gate_rules  # noqa: F401 — register gate_rules side effects
from issuesmith.gates import GATE_REGISTRY

# ---------------------------------------------------------------------------
# (a) All ghdag-registered gate_rules ids are in GATE_REGISTRY
# ---------------------------------------------------------------------------


def test_all_ghdag_gate_rule_ids_in_registry() -> None:
    missing = [gid for gid in _GHDAG_REG if gid not in GATE_REGISTRY]
    assert not missing, (
        f"gate_rules ids registered in ghdag but missing from GATE_REGISTRY: {missing}"
    )


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


@pytest.mark.parametrize("gate_id", ["deps", "scope", "pr_scope", "m2"])
def test_core_gate_ids_present(gate_id: str) -> None:
    assert gate_id in GATE_REGISTRY, f"core gate {gate_id!r} missing from GATE_REGISTRY"


# ---------------------------------------------------------------------------
# (d) README.md gate table matches GATE_REGISTRY
# ---------------------------------------------------------------------------


def _parse_readme_gate_table() -> set[str]:
    """Extract gate ids from the '## Gates' table in README.md."""
    readme = Path(__file__).parents[2] / "README.md"
    if not readme.exists():
        return set()
    text = readme.read_text(encoding="utf-8")
    in_table = False
    ids: set[str] = set()
    for line in text.splitlines():
        if line.startswith("## Gates"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if in_table and "|" in line:
            m = re.match(r"\|\s*`?(\w+)`?\s*\|", line)
            if m and m.group(1) not in ("id", "---"):
                ids.add(m.group(1))
    return ids


def test_readme_gate_table_matches_registry() -> None:
    readme_ids = _parse_readme_gate_table()
    if not readme_ids:
        pytest.skip("README.md has no ## Gates table yet")
    registry_ids = set(GATE_REGISTRY.keys())
    missing_from_readme = registry_ids - readme_ids
    extra_in_readme = readme_ids - registry_ids
    assert not missing_from_readme, (
        f"GATE_REGISTRY ids missing from README ## Gates table: {sorted(missing_from_readme)}"
    )
    assert not extra_in_readme, (
        f"README ## Gates table has ids not in GATE_REGISTRY: {sorted(extra_in_readme)}"
    )
