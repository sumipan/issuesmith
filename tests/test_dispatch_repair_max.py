"""Tests for per-step repair.max in run_requires_loop (#4986, #5787)."""

from __future__ import annotations

from unittest.mock import MagicMock

from issuesmith.config import StepConfig
from issuesmith.gate_registry import Violation


def _v(
    rule_id: str = "lint.E501",
    *,
    location: str | None = None,
    message: str = "line too long",
) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity="fail",
        message=message,
        location=location,
        auto_fixable=False,
        fix_hint="wrap",
    )


def _ctx(tmp_path) -> dict[str, str]:
    return {
        "issue_number": "1",
        "workflow_name": "issuesmith",
        "base_branch": "main",
        "worktree_path": str(tmp_path),
        "allow_paths": "- src/**",
    }


def _fail_gate() -> MagicMock:
    gate = MagicMock()
    gate.check.return_value = [_v()]
    return gate


def test_repair_default_max_is_three() -> None:
    assert StepConfig().repair.max == 3


