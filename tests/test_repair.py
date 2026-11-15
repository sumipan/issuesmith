"""Tests for requires evaluation severity handling in repair (#3922)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from issuesmith.gate_registry import Violation
from issuesmith.repair import evaluate_requires


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


