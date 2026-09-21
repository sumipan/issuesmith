"""Tests for StepResult 3-value contract (Issue #3505).

Covers: construction, compat layer (old exit_code/pipeline_status → new),
assert_preflight_parity with all three status values, and irreversible flag.
"""
from __future__ import annotations

import pytest

from issuesmith.engine import RetryReason, RetrySignal
from issuesmith.steps.base import Andon, StepResult, Verdict

# ---------------------------------------------------------------------------
# New API construction
# ---------------------------------------------------------------------------


class TestStepResultDoneStatus:
    def test_done_with_markers(self):
        r = StepResult(status="done", markers=["M1_DONE"])
        assert r.status == "done"
        assert r.markers == ["M1_DONE"]

    def test_done_default_empty_markers(self):
        r = StepResult(status="done")
        assert r.markers == []
        assert r.status == "done"

    def test_done_multiple_markers(self):
        r = StepResult(status="done", markers=["M1_DONE", "SUB_CREATED"])
        assert len(r.markers) == 2

    def test_done_with_artifacts(self):
        r = StepResult(status="done", markers=["M1_DONE"], artifacts={"key": "val"})
        assert r.artifacts == {"key": "val"}

    def test_done_irreversible(self):
        r = StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)
        assert r.irreversible is True


class TestStepResultRetryStatus:
    def test_retry_with_signal(self):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
        r = StepResult(status="retry", retry=sig)
        assert r.status == "retry"
        assert r.retry is sig

    def test_retry_no_markers_needed(self):
        sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None)
        r = StepResult(status="retry", retry=sig)
        assert r.markers == []


class TestStepResultAndonStatus:
    def test_andon_kind_broken(self):
        r = StepResult(status="andon", andon=Andon(kind="broken"))
        assert r.status == "andon"
        assert r.andon is not None
        assert r.andon.kind == "broken"

    def test_andon_kind_blocked(self):
        r = StepResult(status="andon", andon=Andon(kind="blocked"))
        assert r.andon.kind == "blocked"

    def test_andon_with_summary(self):
        r = StepResult(status="andon", andon=Andon(kind="broken", summary="gate failed"))
        assert r.andon.summary == "gate failed"


# ---------------------------------------------------------------------------
# Compat layer: old exit_code/pipeline_status → new markers
# ---------------------------------------------------------------------------


class TestStepResultCompatLayer:
    def test_old_exit0_pipeline_status_converts_to_markers(self):
        r = StepResult(exit_code=0, pipeline_status="M1_DONE")
        assert "M1_DONE" in r.markers
        assert r.status == "done"

    def test_old_exit0_pipeline_status_worktree_ready(self):
        r = StepResult(exit_code=0, pipeline_status="WORKTREE_READY")
        assert "WORKTREE_READY" in r.markers

    def test_old_exit1_pipeline_status_not_in_markers(self):
        # exit_code=1 failures are not converted to markers
        r = StepResult(exit_code=1, pipeline_status="MERGE_FAILED")
        assert r.exit_code == 1
        assert r.pipeline_status == "MERGE_FAILED"

    def test_old_recovery_preserved(self):
        r = StepResult(exit_code=1, pipeline_status="MERGE_FAILED", recovery="retry later")
        assert r.recovery == "retry later"

    def test_old_exit0_empty_status_no_markers(self):
        r = StepResult(exit_code=0, pipeline_status="")
        assert r.markers == []


# ---------------------------------------------------------------------------
# assert_preflight_parity with new status values
# ---------------------------------------------------------------------------


class TestAssertPreflightParityWithNewStatus:
    """assert_preflight_parity is called from __post_init__; these tests verify
    that all three new status values produce no errors for non-stop markers."""

    def test_done_status_non_stop_marker_passes(self):
        # "WORKTREE_READY" is not a stop status → no error
        r = StepResult(status="done", markers=["WORKTREE_READY"])
        assert r.status == "done"

    def test_retry_status_no_markers_passes(self):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
        r = StepResult(status="retry", retry=sig)
        assert r.status == "retry"

    def test_andon_status_no_markers_passes(self):
        r = StepResult(status="andon", andon=Andon(kind="broken"))
        assert r.status == "andon"

    def test_stop_marker_without_parity_raises(self):
        # A stop marker not in PREFLIGHT_PARITY → ValueError
        with pytest.raises(ValueError, match="preflight parity"):
            StepResult(status="done", markers=["UNKNOWN_FAILED"])

    def test_known_stop_marker_passes(self):
        # MERGE_FAILED is in PREFLIGHT_PARITY
        r = StepResult(status="done", markers=["MERGE_FAILED"])
        assert "MERGE_FAILED" in r.markers

    def test_compat_stop_pipeline_status_checked(self):
        # Old-style exit_code=1 with stop status: parity check applies
        with pytest.raises(ValueError, match="preflight parity"):
            StepResult(exit_code=1, pipeline_status="UNKNOWN_FAILED")

    def test_compat_known_stop_pipeline_status_passes(self):
        r = StepResult(exit_code=1, pipeline_status="MERGE_FAILED")
        assert r.pipeline_status == "MERGE_FAILED"


# ---------------------------------------------------------------------------
# Andon dataclass
# ---------------------------------------------------------------------------


class TestAndonSpec:
    def test_andon_only_kind(self):
        a = Andon(kind="broken")
        assert a.kind == "broken"
        assert a.summary == ""

    def test_andon_with_summary(self):
        a = Andon(kind="decision", summary="choose migration path")
        assert a.summary == "choose migration path"

    def test_andon_kinds(self):
        for kind in ("decision", "blocked", "broken"):
            a = Andon(kind=kind)
            assert a.kind == kind


# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------


class TestVerdict:
    def test_passed(self):
        v = Verdict(passed=True)
        assert v.passed is True
        assert v.reason == ""

    def test_failed_with_reason(self):
        v = Verdict(passed=False, reason="wrong milestone")
        assert v.passed is False
        assert v.reason == "wrong milestone"
