"""Tests for gates.review.ReviewGate (#4986)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.config import ReviewConfig
from issuesmith.engine import RetryReason, RetrySignal
from issuesmith.gates import GateBuildContext, GateBuildError
from issuesmith.gates.review import ReviewGate, _violations_from_stdout, build_review_gate
from issuesmith.repair import evaluate_requires


def _review_cfg() -> ReviewConfig:
    return ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
    )


def test_check_pass_returns_empty(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    with patch(
        "issuesmith.engine.run_guarded_output",
        return_value=(0, "ok\nPIPELINE_STATUS: CP2_PASS\n"),
    ):
        assert gate.check("", []) == []


def test_check_fail_parses_bullets(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    stdout = "x\nProblems:\n- a\n- b\nPIPELINE_STATUS: CP2_FAIL\n"
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 2
    assert vs[0].rule_id == "review.x"
    assert vs[0].message == "a"
    assert vs[0].auto_fixable is False
    assert vs[1].message == "b"


def test_violations_without_heading_use_snippet() -> None:
    vs = _violations_from_stdout("short fail", _review_cfg(), "x")
    assert len(vs) == 1
    assert vs[0].message == "short fail"


def test_check_propagates_retry_signal(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
    with patch("issuesmith.engine.run_guarded_output", side_effect=sig):
        with pytest.raises(RetrySignal):
            gate.check("", [])


def test_evaluate_requires_propagates_retry_signal() -> None:
    gate = MagicMock()
    gate.check.side_effect = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
    with pytest.raises(RetrySignal):
        evaluate_requires({"review": gate}, "", [])


def test_evaluate_requires_gate_error_on_value_error() -> None:
    gate = MagicMock()
    gate.check.side_effect = ValueError("boom")
    result = evaluate_requires({"review": gate}, "", [])
    assert result.gate_error is not None


def test_build_review_gate_missing_step_id(tmp_path: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=tmp_path,
        allow_paths=[],
        base_branch="main",
    )
    with pytest.raises(GateBuildError):
        build_review_gate(ctx)


def test_build_review_gate_missing_review_config(tmp_path: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=tmp_path,
        allow_paths=[],
        base_branch="main",
        step_id="missing",
    )
    with pytest.raises(GateBuildError):
        build_review_gate(ctx)
