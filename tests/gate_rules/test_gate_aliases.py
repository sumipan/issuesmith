"""Gate id aliases: old ids and concept names share rule classes (#5816)."""

from __future__ import annotations

import warnings

import pytest
from ghdag.workflow.gates import GATE_REGISTRY

import issuesmith.gate_rules  # noqa: F401 — alias registration
from issuesmith.config import StepConfig
from issuesmith.gate_rules import GATE_ID_ALIASES
from issuesmith.gates import GateBuildContext, resolve_gate, validate_step_requires


@pytest.mark.parametrize(
    ("old_id", "new_id"),
    list(GATE_ID_ALIASES.items()),
)
def test_alias_points_at_same_gate_class(old_id: str, new_id: str) -> None:
    assert GATE_REGISTRY[old_id] is GATE_REGISTRY[new_id]


def test_resolve_gate_old_and_new_same_violations() -> None:
    body = "## Overview\nno yaml block\n"
    labels: list[str] = []
    ctx = GateBuildContext(worktree_path=None, allow_paths=[], base_branch="main")
    old_gate = resolve_gate("cp1").build(ctx)
    new_gate = resolve_gate("design_lint").build(ctx)
    old_ids = {v.rule_id for v in old_gate.check(body, labels)}
    new_ids = {v.rule_id for v in new_gate.check(body, labels)}
    assert old_ids == new_ids
    assert old_ids


def test_validate_step_requires_warns_on_old_gate_id() -> None:
    steps = {
        "p0": StepConfig(requires=("cp1",), requires_declared=True),
    }
    with pytest.warns(DeprecationWarning, match="cp1.*design_lint"):
        validate_step_requires(steps)


def test_validate_step_requires_silent_on_new_gate_id() -> None:
    steps = {
        "p0": StepConfig(requires=("design_lint",), requires_declared=True),
    }
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        validate_step_requires(steps)
