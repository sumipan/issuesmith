"""Tests for requires evaluation severity handling in repair (#3922)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag.workflow.gates import Violation

from issuesmith.config import StepConfig
from issuesmith.repair import RequiresResult, apply_auto_fixes, evaluate_requires


def _v(
    rule_id: str = "test.fail",
    *,
    severity: str = "fail",
    auto_fixable: bool = False,
) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity=severity,
        message=f"{rule_id} message",
        location=None,
        auto_fixable=auto_fixable,
        fix_hint="fix it" if auto_fixable else None,
    )


def _pass_gate() -> MagicMock:
    g = MagicMock()
    g.check.return_value = []
    return g


def _warn_gate(rule_id: str = "tests.flaky") -> MagicMock:
    g = MagicMock()
    g.check.return_value = [_v(rule_id, severity="warn")]
    return g


class TestEvaluateRequiresSeverity:
    def test_warn_severity_not_blocking(self):
        r = evaluate_requires({"tests": _warn_gate()}, "body", [])
        assert not r.blocking
        assert len(r.warnings) == 1
        assert r.warnings[0].rule_id == "tests.flaky"
        assert r.warnings[0].severity == "warn"

    def test_fail_severity_still_blocking(self):
        g = MagicMock()
        g.check.return_value = [_v("tests.pytest_failure", severity="fail")]
        r = evaluate_requires({"tests": g}, "body", [])
        assert len(r.blocking) == 1
        assert not r.warnings

    def test_mixed_fail_and_warn(self):
        g = MagicMock()
        g.check.return_value = [
            _v("tests.flaky", severity="warn"),
            _v("tests.pytest_failure", severity="fail"),
        ]
        r = evaluate_requires({"tests": g}, "body", [])
        assert [v.rule_id for v in r.blocking] == ["tests.pytest_failure"]
        assert [v.rule_id for v in r.warnings] == ["tests.flaky"]

    def test_apply_auto_fixes_preserves_warnings(self):
        gate = MagicMock()
        gate.fix.side_effect = lambda inp: inp
        result = RequiresResult(
            blocking=[_v(rule_id="lint.E501", auto_fixable=True)],
            warnings=[_v("tests.flaky", severity="warn")],
        )
        updated, _ = apply_auto_fixes(result, {"lint": gate}, MagicMock())
        assert not updated.blocking
        assert len(updated.warnings) == 1
        assert updated.warnings[0].rule_id == "tests.flaky"


class TestEvaluateRequiresRetrySignal:
    def test_retry_signal_reraised(self):
        from issuesmith.engine import RetryReason, RetrySignal

        gate = MagicMock()
        gate.check.side_effect = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
        with pytest.raises(RetrySignal):
            evaluate_requires({"g": gate}, "body", [])

    def test_other_exception_becomes_gate_error(self):
        gate = MagicMock()
        gate.check.side_effect = RuntimeError("broken gate")
        result = evaluate_requires({"g": gate}, "body", [])
        assert result.gate_error is not None


class TestRunRequiresLoopFlaky:
    def test_flaky_only_passes_without_repair(self, tmp_path):
        from issuesmith.ops.dispatch import run_requires_loop

        repair_calls: list = []

        def fake_repair(*args, **kwargs):
            repair_calls.append(args)
            return None

        cfg = StepConfig(
            module="",
            requires=("tests",),
            input_kind="worktree",
            requires_declared=True,
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(tmp_path),
            "allow_paths": "- src/**",
        }
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"tests": _warn_gate()},
            ),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
            patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
            patch("issuesmith.ops.dispatch._run_repair_step", side_effect=fake_repair),
            patch("issuesmith.ops.dispatch._raise_andon") as andon,
            patch("issuesmith.ops.dispatch.get_forge"),
        ):
            rc = run_requires_loop(cfg, "p1", context)

        assert rc is None
        assert repair_calls == []
        andon.assert_not_called()
