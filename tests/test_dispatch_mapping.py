"""Tests for ops/dispatch.py StepResult → exit code + PIPELINE_STATUS mapping (Issue #3505).

Covers: done/retry/andon paths, irreversible gate failure, 3-engine input parity,
and old compat path.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.engine import RetryReason, RetrySignal
from issuesmith.ops.dispatch import map_step_result
from issuesmith.steps.base import Andon, StepResult, Verdict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context(issue_number: str = "42", step_id: str = "p0") -> dict:
    return {
        "issue_number": issue_number,
        "workflow_name": "issuesmith",
        "handler_name": "test_handler",
    }


# ---------------------------------------------------------------------------
# done → exit 0 + PIPELINE_STATUS markers
# ---------------------------------------------------------------------------


class TestDoneMapping:
    def test_done_single_marker_exits_zero(self, capsys):
        rc = map_step_result(
            StepResult(status="done", markers=["M1_DONE"]),
            step_id="m1",
            context=_make_context(),
        )
        assert rc == 0

    def test_done_single_marker_prints_pipeline_status(self, capsys):
        map_step_result(
            StepResult(status="done", markers=["M1_DONE"]),
            step_id="m1",
            context=_make_context(),
        )
        out = capsys.readouterr().out
        assert "PIPELINE_STATUS: M1_DONE" in out

    def test_done_no_markers_exits_zero_no_output(self, capsys):
        rc = map_step_result(
            StepResult(status="done"),
            step_id="m1",
            context=_make_context(),
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "PIPELINE_STATUS" not in out

    def test_done_multiple_markers_all_printed(self, capsys):
        map_step_result(
            StepResult(status="done", markers=["WORKTREE_READY", "SUB_CREATED"]),
            step_id="p0",
            context=_make_context(),
        )
        out = capsys.readouterr().out
        assert "PIPELINE_STATUS: WORKTREE_READY" in out
        assert "PIPELINE_STATUS: SUB_CREATED" in out


# ---------------------------------------------------------------------------
# retry → RetrySignal raised (exit 0 + defer in caller)
# ---------------------------------------------------------------------------


class TestRetryMapping:
    def test_retry_raises_retry_signal(self):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
        with pytest.raises(RetrySignal):
            map_step_result(
                StepResult(status="retry", retry=sig),
                step_id="p0",
                context=_make_context(),
            )

    def test_retry_raises_same_signal_instance(self):
        sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None)
        with pytest.raises(RetrySignal) as exc_info:
            map_step_result(
                StepResult(status="retry", retry=sig),
                step_id="p0",
                context=_make_context(),
            )
        assert exc_info.value is sig

    def test_retry_reason_preserved(self):
        sig = RetrySignal(reason=RetryReason.ENGINE_ENVIRONMENT_ERROR, after=None)
        with pytest.raises(RetrySignal) as exc_info:
            map_step_result(
                StepResult(status="retry", retry=sig),
                step_id="p0",
                context=_make_context(),
            )
        assert exc_info.value.reason == RetryReason.ENGINE_ENVIRONMENT_ERROR


# ---------------------------------------------------------------------------
# andon → exit 1 + raise_andon
# ---------------------------------------------------------------------------


class TestAndonMapping:
    def test_andon_exits_one(self):
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
             patch("issuesmith.ops.dispatch._raise_andon") as mock_raise:
            mock_forge.return_value = MagicMock()
            rc = map_step_result(
                StepResult(status="andon", andon=Andon(kind="broken")),
                step_id="m2",
                context=_make_context(),
            )
        assert rc == 1

    def test_andon_calls_raise_andon(self):
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
             patch("issuesmith.ops.dispatch._raise_andon") as mock_raise:
            mock_forge.return_value = MagicMock()
            map_step_result(
                StepResult(status="andon", andon=Andon(kind="broken")),
                step_id="m2",
                context=_make_context(),
            )
        mock_raise.assert_called_once()

    def test_andon_kind_propagated(self):
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
             patch("issuesmith.ops.dispatch._raise_andon") as mock_raise:
            mock_forge.return_value = MagicMock()
            map_step_result(
                StepResult(status="andon", andon=Andon(kind="decision")),
                step_id="m2",
                context=_make_context(),
            )
        _, full_andon = mock_raise.call_args[0]
        assert full_andon.kind == "decision"

    def test_andon_broken_kind(self):
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
             patch("issuesmith.ops.dispatch._raise_andon") as mock_raise:
            mock_forge.return_value = MagicMock()
            map_step_result(
                StepResult(status="andon", andon=Andon(kind="broken")),
                step_id="p0",
                context=_make_context(),
            )
        _, full_andon = mock_raise.call_args[0]
        assert full_andon.kind == "broken"


# ---------------------------------------------------------------------------
# irreversible gate failure → andon(broken), no merge
# ---------------------------------------------------------------------------


class TestIrreversibleGateMapping:
    def test_irreversible_with_failed_verdict_returns_andon(self):
        verdicts = [Verdict(passed=False, reason="milestone mismatch")]
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
             patch("issuesmith.ops.dispatch._raise_andon") as mock_raise:
            mock_forge.return_value = MagicMock()
            rc = map_step_result(
                StepResult(status="done", markers=["MERGE_DONE"], irreversible=True),
                step_id="m2",
                context=_make_context(),
                verdicts=verdicts,
            )
        assert rc == 1
        mock_raise.assert_called_once()
        _, full_andon = mock_raise.call_args[0]
        assert full_andon.kind == "broken"

    def test_irreversible_with_all_passing_verdicts_proceeds(self, capsys):
        verdicts = [Verdict(passed=True), Verdict(passed=True)]
        with patch("issuesmith.ops.dispatch.get_forge"):
            rc = map_step_result(
                StepResult(status="done", markers=["MERGE_DONE"], irreversible=True),
                step_id="m2",
                context=_make_context(),
                verdicts=verdicts,
            )
        assert rc == 0
        out = capsys.readouterr().out
        assert "PIPELINE_STATUS: MERGE_DONE" in out

    def test_irreversible_with_no_verdicts_proceeds(self, capsys):
        rc = map_step_result(
            StepResult(status="done", markers=["MERGE_DONE"], irreversible=True),
            step_id="m2",
            context=_make_context(),
            verdicts=None,
        )
        assert rc == 0

    def test_reversible_with_failed_verdict_ignored(self, capsys):
        verdicts = [Verdict(passed=False, reason="check failed")]
        rc = map_step_result(
            StepResult(status="done", markers=["WORKTREE_READY"], irreversible=False),
            step_id="p0",
            context=_make_context(),
            verdicts=verdicts,
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# 3-engine input parity: claude / cursor / codex produce the same result
# ---------------------------------------------------------------------------


class TestEngineInputParity:
    """Mapping logic is engine-agnostic: same StepResult → same output for all engines."""

    ENGINES = ["claude", "cursor", "codex"]

    def test_done_result_same_across_engines(self, capsys):
        results = []
        for engine in self.ENGINES:
            ctx = {**_make_context(), "engine": engine}
            rc = map_step_result(
                StepResult(status="done", markers=["M1_DONE"]),
                step_id="m1",
                context=ctx,
            )
            out = capsys.readouterr().out
            results.append((rc, out))
        assert all(r == results[0] for r in results), "all engines must produce same result"

    def test_retry_result_same_across_engines(self):
        results = []
        for engine in self.ENGINES:
            ctx = {**_make_context(), "engine": engine}
            sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
            try:
                map_step_result(
                    StepResult(status="retry", retry=sig),
                    step_id="p0",
                    context=ctx,
                )
                results.append("no_raise")
            except RetrySignal:
                results.append("raised")
        assert results == ["raised"] * len(self.ENGINES)

    def test_andon_result_same_across_engines(self):
        results = []
        for engine in self.ENGINES:
            ctx = {**_make_context(), "engine": engine}
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
                 patch("issuesmith.ops.dispatch._raise_andon"):
                mock_forge.return_value = MagicMock()
                rc = map_step_result(
                    StepResult(status="andon", andon=Andon(kind="broken")),
                    step_id="m2",
                    context=ctx,
                )
            results.append(rc)
        assert results == [1] * len(self.ENGINES)


# ---------------------------------------------------------------------------
# Old compat path in dispatch
# ---------------------------------------------------------------------------


class TestOldCompatMapping:
    def test_old_exit0_pipeline_status_exits_zero(self, capsys):
        rc = map_step_result(
            StepResult(exit_code=0, pipeline_status="M1_DONE"),
            step_id="m1",
            context=_make_context(),
        )
        assert rc == 0

    def test_old_exit0_prints_pipeline_status(self, capsys):
        map_step_result(
            StepResult(exit_code=0, pipeline_status="M1_DONE"),
            step_id="m1",
            context=_make_context(),
        )
        out = capsys.readouterr().out
        assert "PIPELINE_STATUS: M1_DONE" in out

    def test_old_exit1_pipeline_status_exits_one(self, capsys):
        rc = map_step_result(
            StepResult(exit_code=1, pipeline_status="MERGE_FAILED"),
            step_id="m2",
            context=_make_context(),
        )
        assert rc == 1

    def test_old_exit1_prints_pipeline_status(self, capsys):
        map_step_result(
            StepResult(exit_code=1, pipeline_status="MERGE_FAILED"),
            step_id="m2",
            context=_make_context(),
        )
        out = capsys.readouterr().out
        assert "PIPELINE_STATUS: MERGE_FAILED" in out

    def test_old_recovery_comment_posted(self):
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
            client = MagicMock()
            mock_forge.return_value = client
            map_step_result(
                StepResult(exit_code=1, pipeline_status="MERGE_FAILED", recovery="please check"),
                step_id="m2",
                context=_make_context(issue_number="99"),
            )
        client.issue_comment.assert_called_once_with(99, "please check")


# ---------------------------------------------------------------------------
# run_guarded *_SKIPPED validation
# ---------------------------------------------------------------------------


class TestRunGuardedSkippedValidation:
    def test_skipped_in_success_statuses_raises_value_error(self):
        from issuesmith.engine import run_guarded
        with pytest.raises(ValueError, match="SKIPPED"):
            run_guarded(
                role="design",
                template_path="fake.md",
                variables=[],
                success_statuses=["FOO_SKIPPED"],
                failure_status="FOO_FAILED",
            )

    def test_skipped_suffix_variant_raises(self):
        from issuesmith.engine import run_guarded
        with pytest.raises(ValueError, match="SKIPPED"):
            run_guarded(
                role="design",
                template_path="fake.md",
                variables=[],
                success_statuses=["M1_SKIPPED", "M1_DONE"],
                failure_status="M1_FAILED",
            )

    def test_normal_success_statuses_no_error_before_exec(self):
        from issuesmith.engine import run_guarded
        # Should not raise ValueError (will fail at template load, but that's OK)
        with pytest.raises(Exception) as exc_info:
            run_guarded(
                role="design",
                template_path="nonexistent.md",
                variables=[],
                success_statuses=["M1_DONE"],
                failure_status="M1_FAILED",
            )
        # ValueError about SKIPPED must NOT be raised
        assert "SKIPPED" not in str(exc_info.value)
