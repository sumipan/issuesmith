"""Tests for ops/dispatch.py StepResult → exit code + PIPELINE_STATUS mapping (Issue #3505).

Covers: done/retry/andon paths, irreversible gate failure, 3-engine input parity,
and old compat path.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag.core import reply_block

from issuesmith.contract import Andon, StepResult, Verdict
from issuesmith.engine import RetryReason, RetrySignal
from issuesmith.ops.dispatch import map_step_result

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

    def test_merge_done_marker_emits_ok_reply_block(self, capsys):
        """Refs #7553 AC4 — StepResult marker becomes one ok reply block."""
        rc = map_step_result(
            StepResult(status="done", markers=["MERGE_DONE"]),
            step_id="m2",
            context=_make_context(),
        )
        assert rc == 0
        out = capsys.readouterr().out
        block = reply_block.parse_reply_block(out)
        assert block is not None and block.status == "ok" and block.code == "MERGE_DONE"
        assert out.count("```ghdag") == 1
        assert "PIPELINE_STATUS:" not in out


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


# ---------------------------------------------------------------------------
# andon → exit 1 + raise_andon
# ---------------------------------------------------------------------------


class TestAndonMapping:


    def test_andon_id_uses_label_namespace_when_workflow_name_missing(self):
        with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, patch(
            "issuesmith.ops.dispatch.get_config"
        ) as mock_cfg, patch("issuesmith.ops.dispatch._raise_andon") as mock_raise:
            mock_forge.return_value = MagicMock()
            mock_cfg.return_value.label_namespace = "issuesmith"
            map_step_result(
                StepResult(status="andon", andon=Andon(kind="broken")),
                step_id="p1",
                context={"issue_number": "42"},
            )
        _, full_andon = mock_raise.call_args[0]
        assert full_andon.id.startswith("issuesmith:42:p1:")


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
        block = reply_block.parse_reply_block(out)
        assert block is not None
        assert block.status == "ok" and block.code == "MERGE_DONE"
        assert "PIPELINE_STATUS:" not in out


# ---------------------------------------------------------------------------
# 3-engine input parity: claude / cursor / codex produce the same result
# ---------------------------------------------------------------------------


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


    def test_old_exit1_pipeline_status_exits_one(self, capsys):
        rc = map_step_result(
            StepResult(exit_code=1, pipeline_status="MERGE_FAILED"),
            step_id="m2",
            context=_make_context(),
        )
        assert rc == 1


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


# ---------------------------------------------------------------------------
# andon → projected through raise_andon (#4807)
# ---------------------------------------------------------------------------


def test_map_step_result_repair_andon_facts():
    """Refs #8075 AC-8."""
    with patch("issuesmith.ops.dispatch.get_forge") as mock_forge, patch(
        "issuesmith.ops.dispatch._raise_andon"
    ) as mock_raise:
        mock_forge.return_value = MagicMock()
        map_step_result(
            StepResult(
                status="andon",
                andon=Andon(kind="decision", rule_id="repair.gave_up"),
            ),
            step_id="repair",
            context={
                "issue_number": "42",
                "repair_step_origin": "cp2",
            },
        )
    _, full_andon = mock_raise.call_args[0]
    assert full_andon.facts == {
        "failed_step": "cp2",
        "trigger": "step",
        "rule_ids": ["repair.gave_up"],
    }


def test_dispatch_main_invalid_context_uses_fail_contract(capsys):
    """Refs #8129 — dispatch parse errors use error[INVALID_VALUE] on stderr."""
    from issuesmith.ops import dispatch

    rc = dispatch.main(["p0", "not_a_key_value"])
    assert rc == 2
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[INVALID_VALUE]:")


def test_dispatch_main_reraises_retry_signal():
    """Refs #8129 — RetrySignal still propagates from dispatch main."""
    from issuesmith.engine import RetryReason, RetrySignal
    from issuesmith.ops import dispatch

    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
    with patch("issuesmith.ops.dispatch._try_python_step", return_value=None), patch(
        "issuesmith.ops.dispatch._run_bash_step",
        side_effect=sig,
    ), patch("issuesmith.ops.dispatch._handle_retry_signal") as mock_handle:
        rc = dispatch.main(["p0", "issue_number=1"])
    assert rc == 0
    mock_handle.assert_called_once()


class _LabelForge:
    """In-memory forge holding one Issue's labels."""

    def __init__(self, labels: set[str]) -> None:
        self.labels = set(labels)
        self.comments: list[str] = []

    def issue_get(self, number, fields=None):
        return {"number": number, "labels": [{"name": lb} for lb in sorted(self.labels)]}

    def issue_update(self, number, **kwargs):
        self.labels |= set(kwargs.get("labels_add") or [])
        self.labels -= set(kwargs.get("labels_remove") or [])

    def issue_comment(self, number, body):
        self.comments.append(body)


