"""Tests for per-step repair.max in run_requires_loop (#4986)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ghdag.workflow.gates import Violation

from issuesmith.config import RepairConfig, StepConfig
from issuesmith.ops.dispatch import run_requires_loop


def _fail_gate() -> MagicMock:
    gate = MagicMock()
    gate.check.return_value = [
        Violation(
            rule_id="lint.E501",
            severity="fail",
            message="line too long",
            location=None,
            auto_fixable=False,
            fix_hint="wrap",
        )
    ]
    return gate


def test_repair_max_one_andon_after_single_repair(tmp_path) -> None:
    cfg = StepConfig(
        requires=("lint",),
        input_kind="worktree",
        requires_declared=True,
        repair=RepairConfig(max=1),
    )
    context = {
        "issue_number": "1",
        "workflow_name": "issuesmith",
        "base_branch": "main",
        "worktree_path": str(tmp_path),
        "allow_paths": "- src/**",
    }
    repair_calls: list[int] = []

    def fake_repair(*_args, **_kwargs):
        repair_calls.append(1)
        return None

    with (
        patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"lint": _fail_gate()},
        ),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", side_effect=fake_repair),
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(cfg, "p1", context)

    assert rc == 1
    assert len(repair_calls) == 1
    andon.assert_called_once()
    assert andon.call_args[0][1].kind == "decision"


def test_repair_default_max_is_three() -> None:
    assert StepConfig().repair.max == 3
