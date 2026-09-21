"""Tests for RetrySignal deferred retry behavior (Issue #3485).

Verifies that _execute() raises RetrySignal immediately when engines are
paused (removing the 6h wait loop), and that dispatch.main() catches it
to register a defer via QuotaGate.defer, apply the <ns>:waiting label, and
return exit code 0 (no DEP_FAILED).

claude / cursor / codex adapters all produce the same RetrySignal (parent AC-7).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import ManagedResult
from ghdag.metrics.models import FailureClass
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.engine import RetryReason, RetrySignal, RoleSelection, _execute


# ---- helpers ----

def _paused(resume_at, *, reason="quota"):
    return EngineQuotaState(
        status="paused",
        observed_at=datetime.now(timezone.utc),
        resume_at=resume_at,
        reason=reason,
    )


def _available():
    return EngineQuotaState(
        status="available",
        observed_at=datetime.now(timezone.utc),
        resume_at=None,
        reason=None,
    )


def _snap(engines):
    return QuotaSnapshot(
        engines=engines,
        deferred_tasks={},
        draining_engines={},
        running_tasks={},
    )


def _result(engine="claude", *, rc=0, body="ok", failure_class=None):
    return ManagedResult(
        body=body,
        usage=None,
        returncode=rc,
        failure_class=failure_class,
        engine_used=engine,
        model_used="m",
        attempts=1,
        quota_reported=False,
        additional_tags={},
    )


@pytest.fixture
def exe_mocks(monkeypatch):
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    cm = MagicMock(return_value=_result())
    monkeypatch.setattr("issuesmith.engine.call_managed", cm)
    monkeypatch.setattr("issuesmith.engine.time.sleep", MagicMock())
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    return {"call_managed": cm, "quota_gate": qg}


# ==================== RetrySignal class ====================

class TestRetrySignalClass:
    def test_is_runtime_error(self):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
        assert isinstance(sig, RuntimeError)

    def test_carries_reason_quota_paused(self):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
        assert sig.reason == RetryReason.QUOTA_PAUSED

    def test_carries_reason_rate_limited(self):
        sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None)
        assert sig.reason == RetryReason.RATE_LIMITED

    def test_carries_after_datetime(self):
        after = datetime.now(timezone.utc) + timedelta(hours=2)
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=after, role="design")
        assert sig.after == after

    def test_after_none_allowed(self):
        sig = RetrySignal(reason=RetryReason.ENGINE_ENVIRONMENT_ERROR, after=None)
        assert sig.after is None

    def test_quota_paused_message_matches_legacy_pattern(self):
        # AC-3/AC-5 compat: old tests use pytest.raises(RuntimeError, match=...)
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
        assert "All engines paused for role design" in str(sig)


# ==================== engine._execute() raises RetrySignal ====================

class TestExecuteAllEnginesPaused:
    """When all engines are paused, _execute() raises immediately (no wait loop)."""

    def test_raises_retry_signal_no_sleep(self, exe_mocks):
        snap = _snap({"claude": _paused(None), "codex": _paused(None)})
        exe_mocks["quota_gate"].snapshot.return_value = snap

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.QUOTA_PAUSED
        exe_mocks["call_managed"].assert_not_called()

    def test_after_is_earliest_resume_at(self, exe_mocks):
        now = datetime.now(timezone.utc)
        soon = now + timedelta(hours=1)
        later = now + timedelta(hours=5)
        snap = _snap({"claude": _paused(soon), "codex": _paused(later)})
        exe_mocks["quota_gate"].snapshot.return_value = snap

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.after == soon

    def test_after_none_when_no_resume_at(self, exe_mocks):
        snap = _snap({"claude": _paused(None), "codex": _paused(None)})
        exe_mocks["quota_gate"].snapshot.return_value = snap

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.after is None

    def test_fallback_available_no_retry_signal(self, exe_mocks):
        """Primary paused + fallback available → use fallback, no RetrySignal."""
        snap = _snap({"claude": _paused(None), "codex": _available()})
        exe_mocks["quota_gate"].snapshot.return_value = snap
        exe_mocks["call_managed"].return_value = _result("codex")

        result = _execute("design", "prompt")
        assert result.returncode == 0
        assert exe_mocks["call_managed"].call_args.kwargs["engine"] == "codex"


# ==================== 3 adapter × RetrySignal (parent AC-7) ====================

class TestAdapterUniformRetrySignal:
    """claude / cursor / codex adapters each raise RetrySignal when paused."""

    @pytest.mark.parametrize("primary_engine", ["claude", "codex"])
    def test_standard_engine_raises_quota_paused(self, exe_mocks, monkeypatch, primary_engine):
        monkeypatch.setattr(
            "issuesmith.engine.resolve",
            lambda role, tier=None: RoleSelection(engine=primary_engine, model="m"),
        )
        # Both standard engines paused → no fallback
        snap = _snap({"claude": _paused(None), "codex": _paused(None)})
        exe_mocks["quota_gate"].snapshot.return_value = snap

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.QUOTA_PAUSED

    def test_cursor_engine_raises_quota_paused(self, exe_mocks, monkeypatch):
        monkeypatch.setattr(
            "issuesmith.engine.resolve",
            lambda role, tier=None: RoleSelection(engine="cursor", model="auto"),
        )
        monkeypatch.setattr(
            "issuesmith.engine.ROLE_ENGINES",
            {
                "design": frozenset({"claude", "cursor", "codex"}),
                "implementation": frozenset({"claude", "cursor", "codex"}),
            },
        )
        snap = _snap({
            "claude": _paused(None),
            "cursor": _paused(None),
            "codex": _paused(None),
        })
        exe_mocks["quota_gate"].snapshot.return_value = snap

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.QUOTA_PAUSED


# ==================== Rate limit → RetrySignal ====================

class TestRateLimitedRetrySignal:
    def test_rate_limited_no_available_fallback_raises(self, exe_mocks, monkeypatch):
        """Rate limit detected + no available fallback → RetrySignal(RATE_LIMITED)."""
        all_available = _snap({"claude": _available(), "codex": _available()})
        # After quota.report(), codex also paused → no alt available
        all_paused = _snap({"claude": _paused(None), "codex": _paused(None)})
        exe_mocks["quota_gate"].snapshot.side_effect = [all_available, all_paused]
        exe_mocks["call_managed"].return_value = _result(
            "claude", rc=1, body="rate limit exceeded"
        )
        monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda body: True)

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.RATE_LIMITED
        exe_mocks["quota_gate"].report.assert_called_once()

    def test_rate_limited_with_fallback_uses_fallback(self, exe_mocks, monkeypatch):
        """Rate limit + available fallback → retry with fallback (no RetrySignal)."""
        all_available = _snap({"claude": _available(), "codex": _available()})
        exe_mocks["quota_gate"].snapshot.return_value = all_available
        rate_limited = _result("claude", rc=1, body="rate limit")
        exe_mocks["call_managed"].side_effect = [rate_limited, _result("codex")]
        monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda body: True)

        result = _execute("design", "prompt")
        assert result.returncode == 0
        assert result.stdout == "ok"


# ==================== ENGINE_ENVIRONMENT_ERROR → RetrySignal ====================

class TestEngineEnvironmentError:
    def test_env_error_no_fallback_raises(self, exe_mocks, monkeypatch):
        """ENGINE_ENVIRONMENT_ERROR with no fallback → RetrySignal."""
        snap = _snap({"claude": _available(), "codex": _available()})
        exe_mocks["quota_gate"].snapshot.return_value = snap
        exe_mocks["call_managed"].return_value = _result(
            "claude", rc=1, failure_class=FailureClass.ENGINE_ENVIRONMENT_ERROR.value
        )
        monkeypatch.setattr(
            "issuesmith.engine.ROLE_ENGINES",
            {"design": frozenset({"claude"}), "implementation": frozenset({"claude"})},
        )

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.ENGINE_ENVIRONMENT_ERROR

    def test_env_error_with_fallback_uses_fallback(self, exe_mocks):
        """ENGINE_ENVIRONMENT_ERROR + available fallback → switch to fallback."""
        snap = _snap({"claude": _available(), "codex": _available()})
        exe_mocks["quota_gate"].snapshot.return_value = snap
        env_error = _result(
            "claude", rc=1, failure_class=FailureClass.ENGINE_ENVIRONMENT_ERROR.value
        )
        exe_mocks["call_managed"].side_effect = [env_error, _result("codex")]

        result = _execute("design", "prompt")
        assert result.returncode == 0


# ==================== dispatch.main() handles RetrySignal ====================

@pytest.fixture
def dispatch_mocks(monkeypatch, tmp_path):
    from issuesmith.ops import dispatch

    quota_gate_mock = MagicMock()
    forge_mock = MagicMock()
    cfg_mock = MagicMock()
    cfg_mock.label_namespace = "issuesmith"
    cfg_mock.paths.quota_state = tmp_path / "quota.json"

    monkeypatch.setattr(dispatch, "_STEP_MODULES", {})
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.QuotaGate",
        MagicMock(return_value=quota_gate_mock),
    )
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.get_forge",
        MagicMock(return_value=forge_mock),
    )
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.get_config",
        MagicMock(return_value=cfg_mock),
    )
    return {
        "dispatch": dispatch,
        "quota_gate": quota_gate_mock,
        "forge": forge_mock,
        "cfg": cfg_mock,
    }


class TestDispatchHandlesRetrySignal:
    def test_catches_retry_signal_returns_exit_0(self, dispatch_mocks, monkeypatch):
        dispatch = dispatch_mocks["dispatch"]
        after = datetime.now(timezone.utc) + timedelta(hours=1)
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=after, role="design")
        monkeypatch.setattr(dispatch, "_try_python_step", MagicMock(side_effect=sig))

        rc = dispatch.main(["test-step", "issue_number=42"])

        assert rc == 0

    def test_calls_quota_gate_defer(self, dispatch_mocks, monkeypatch):
        dispatch = dispatch_mocks["dispatch"]
        after = datetime.now(timezone.utc) + timedelta(hours=2)
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=after, role="design")
        monkeypatch.setattr(dispatch, "_try_python_step", MagicMock(side_effect=sig))

        dispatch.main(["test-step", "issue_number=42"])

        dispatch_mocks["quota_gate"].defer.assert_called_once_with(
            "test-step", after=after
        )

    def test_applies_waiting_label(self, dispatch_mocks, monkeypatch):
        dispatch = dispatch_mocks["dispatch"]
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
        monkeypatch.setattr(dispatch, "_try_python_step", MagicMock(side_effect=sig))

        dispatch.main(["test-step", "issue_number=42"])

        dispatch_mocks["forge"].issue_update.assert_called_with(
            42, labels_add=["issuesmith:waiting"]
        )

    def test_removes_waiting_label_on_normal_rerun(self, dispatch_mocks, monkeypatch):
        """On normal step execution (no RetrySignal), waiting label is removed at start."""
        dispatch = dispatch_mocks["dispatch"]
        monkeypatch.setattr(dispatch, "_try_python_step", MagicMock(return_value=0))

        dispatch.main(["test-step", "issue_number=42"])

        dispatch_mocks["forge"].remove_label.assert_called_with(
            42, "issuesmith:waiting"
        )

    def test_no_issue_number_skips_label_ops(self, dispatch_mocks, monkeypatch):
        """When context has no issue_number, label operations are skipped."""
        dispatch = dispatch_mocks["dispatch"]
        monkeypatch.setattr(dispatch, "_try_python_step", MagicMock(return_value=0))

        rc = dispatch.main(["test-step"])

        assert rc == 0
        dispatch_mocks["forge"].remove_label.assert_not_called()

    def test_retry_signal_without_issue_number_still_returns_0(
        self, dispatch_mocks, monkeypatch
    ):
        dispatch = dispatch_mocks["dispatch"]
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
        monkeypatch.setattr(dispatch, "_try_python_step", MagicMock(side_effect=sig))

        rc = dispatch.main(["test-step"])
        assert rc == 0
