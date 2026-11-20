"""Tests for accepts evaluation in dispatch map_step_result (#4804 / #4886)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from issuesmith.config import StepConfig
from issuesmith.contract import StepResult
from issuesmith.gate_registry import Violation


def _v(rule_id: str = "test.fail", auto_fixable: bool = False) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity="fail",
        message="test violation",
        location=None,
        auto_fixable=auto_fixable,
        fix_hint="fix it" if auto_fixable else None,
    )


def _pass_gate() -> MagicMock:
    g = MagicMock()
    g.check.return_value = []
    return g


def _fail_gate(rule_id: str = "test.fail", auto_fixable: bool = False) -> MagicMock:
    g = MagicMock()
    g.check.return_value = [_v(rule_id, auto_fixable)]
    return g


def _make_ctx(issue: str = "42") -> dict:
    return {
        "issue_number": issue,
        "workflow_name": "issuesmith",
        "base_branch": "main",
    }


def _map_done(cfg: StepConfig, **kwargs):
    from issuesmith.ops.dispatch import map_step_result

    return map_step_result(
        StepResult(status="done", markers=["IMPL_DONE"]),
        step_id="p2",
        context=_make_ctx(),
        step_cfg=cfg,
        **kwargs,
    )


class TestDispatchAcceptsPass:
    def test_accepts_only_step_pass_emits_markers(self, capsys):
        cfg = StepConfig(module="issuesmith.steps.test", accepts=("lint",))
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"lint": _pass_gate()},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                rc = _map_done(cfg)
        assert rc == 0
        from ghdag.core.reply_block import parse_reply_block

        block = parse_reply_block(capsys.readouterr().out)
        assert block is not None and block.status == "ok" and block.code == "IMPL_DONE"


