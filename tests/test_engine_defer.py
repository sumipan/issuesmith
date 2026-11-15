"""Tests for RetrySignal deferred retry behavior (Issue #3485).

Verifies that _execute() raises RetrySignal immediately when engines are
paused (removing the 6h wait loop), and that dispatch.main() catches it
to hand the task back to ghdag as WAIT_QUOTA, project the <ns>:waiting label (asserted
as the net add / remove label diff, not forge call counts), and return exit
code 0 (no DEP_FAILED).

claude / cursor / codex adapters all produce the same RetrySignal (parent AC-7).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.metrics import FailureClass
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
        draining_engines={},
        running_tasks={},
        limits={},
    )


def _result(engine="claude", *, rc=0, body="ok", failure_class=None):
    return LLMResult(stdout="", stderr="",
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
    monkeypatch.setattr("issuesmith.engine.call", cm)
    monkeypatch.setattr("issuesmith.engine.time.sleep", MagicMock())
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    return {"call": cm, "quota_gate": qg}


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


    def test_after_is_earliest_resume_at(self, exe_mocks):
        now = datetime.now(timezone.utc)
        soon = now + timedelta(hours=1)
        later = now + timedelta(hours=5)
        snap = _snap({"claude": _paused(soon), "codex": _paused(later)})
        exe_mocks["quota_gate"].snapshot.return_value = snap

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.after == soon


# ==================== 3 adapter × RetrySignal (parent AC-7) ====================


# ==================== Rate limit → RetrySignal ====================

class TestRateLimitedRetrySignal:
    def test_rate_limited_no_available_fallback_raises(self, exe_mocks, monkeypatch):
        """Rate limit detected + no available fallback → RetrySignal(RATE_LIMITED)."""
        all_available = _snap({"claude": _available(), "codex": _available()})
        # After quota.report(), codex also paused → no alt available
        all_paused = _snap({"claude": _paused(None), "codex": _paused(None)})
        exe_mocks["quota_gate"].snapshot.side_effect = [all_available, all_paused]
        exe_mocks["call"].return_value = _result(
            "claude", rc=1, body="rate limit exceeded"
        )
        monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda body: True)

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.RATE_LIMITED
        exe_mocks["quota_gate"].report.assert_called_once()

# ==================== ENGINE_ENVIRONMENT_ERROR → RetrySignal ====================

class TestEngineEnvironmentError:
    def test_env_error_no_fallback_raises(self, exe_mocks, monkeypatch):
        """ENGINE_ENVIRONMENT_ERROR with no fallback → RetrySignal."""
        snap = _snap({"claude": _available(), "codex": _available()})
        exe_mocks["quota_gate"].snapshot.return_value = snap
        exe_mocks["call"].return_value = _result(
            "claude", rc=1, failure_class=FailureClass.ENGINE_ENVIRONMENT_ERROR
        )
        monkeypatch.setattr(
            "issuesmith.engine.ROLE_ENGINES",
            {"design": ("claude",), "implementation": ("claude",)},
        )

        with pytest.raises(RetrySignal) as exc_info:
            _execute("design", "prompt")

        assert exc_info.value.reason == RetryReason.ENGINE_ENVIRONMENT_ERROR

# ==================== dispatch.main() handles RetrySignal ====================

_NS = "issuesmith"


class _FakeForge:
    """In-memory forge: records label state so tests assert on the projected diff.

    Assertions use the net add / remove sets between the initial and final label
    state of each issue, not how many times (or through which API) labels changed.
    """

    def __init__(self) -> None:
        self._initial: dict[int, set[str]] = {}
        self._labels: dict[int, set[str]] = {}
        self._other = MagicMock()

    def seed(self, issue_number: int, labels: set[str]) -> None:
        self._initial[issue_number] = set(labels)
        self._labels[issue_number] = set(labels)

    def _current(self, issue_number: int) -> set[str]:
        self._initial.setdefault(issue_number, set())
        return self._labels.setdefault(issue_number, set())

    def add_label(self, issue_number, label, *args, **kwargs):
        self._current(issue_number).add(label)

    def remove_label(self, issue_number, label, *args, **kwargs):
        self._current(issue_number).discard(label)

    def issue_update(self, issue_number, *args, labels_add=None, labels_remove=None, **kwargs):
        current = self._current(issue_number)
        current.update(labels_add or ())
        current.difference_update(labels_remove or ())

    def issue_get(self, issue_number, *args, **kwargs):
        names = sorted(self._current(issue_number))
        return {"number": issue_number, "labels": [{"name": n} for n in names]}

    def diff(self, issue_number: int) -> tuple[set[str], set[str]]:
        initial = self._initial.get(issue_number, set())
        final = self._labels.get(issue_number, set())
        return final - initial, initial - final

    def touched(self) -> set[int]:
        return {n for n in self._labels if self.diff(n) != (set(), set())}

    def __getattr__(self, name):
        return getattr(self._other, name)


@pytest.fixture
def dispatch_mocks(monkeypatch, tmp_path):
    from issuesmith.ops import dispatch

    forge = _FakeForge()
    cfg_mock = MagicMock()
    cfg_mock.label_namespace = _NS
    cfg_mock.paths.quota_state = tmp_path / "quota.json"

    monkeypatch.setattr(dispatch, "_STEP_MODULES", {})
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.get_forge",
        MagicMock(return_value=forge),
    )
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.get_config",
        MagicMock(return_value=cfg_mock),
    )
    return {
        "dispatch": dispatch,
        "forge": forge,
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


# ==================== engine._run_guarded_with_requires() ====================


class TestRunGuardedWithRequiresRetrySignal:
    """RetrySignal at pre/LLM/post boundaries -> WAIT_QUOTA via handle_retry_signal."""

    @pytest.fixture
    def guarded_mocks(self, monkeypatch):
        from issuesmith.ops import dispatch

        step_cfg = MagicMock()
        step_cfg.accepts = ("accepts-rule",)
        monkeypatch.setattr(
            "issuesmith.ops.dispatch.resolve_step_config",
            MagicMock(return_value=step_cfg),
        )
        handle = MagicMock()
        monkeypatch.setattr(dispatch, "handle_retry_signal", handle)
        return {"handle": handle, "step_cfg": step_cfg}

    def _call(self, **patch_targets):
        from issuesmith.engine import _run_guarded_with_requires

        defaults = {
            "pre_rc": None,
            "emit_rc": (0, "PIPELINE_STATUS: IMPL_DONE\n"),
            "requires_rc": None,
            "accepts_rc": None,
        }
        defaults.update(patch_targets)
        return _run_guarded_with_requires(
            "implementation",
            "/tmp/t.md",
            ["issue_number=5802"],
            ["IMPL_DONE"],
            "IMPL_FAILED",
            cwd=None,
            tier=None,
            emit_status=None,
            requires_step="p1",
        )

    def test_pre_gate_retry_uses_step_and_issue(
        self, monkeypatch, guarded_mocks
    ):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="implementation")
        monkeypatch.setattr(
            "issuesmith.engine._run_pre_gate_phase",
            MagicMock(side_effect=sig),
        )

        rc = self._call()

        assert rc == 0
        guarded_mocks["handle"].assert_called_once_with(sig, "p1", 5802)

    def test_llm_body_retry_uses_step_and_issue(
        self, monkeypatch, guarded_mocks
    ):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="implementation")
        monkeypatch.setattr(
            "issuesmith.engine._run_pre_gate_phase",
            MagicMock(return_value=None),
        )
        monkeypatch.setattr(
            "issuesmith.engine._run_guarded_order",
            MagicMock(side_effect=sig),
        )

        rc = self._call()

        assert rc == 0
        guarded_mocks["handle"].assert_called_once_with(sig, "p1", 5802)

    def test_post_requires_retry(
        self, monkeypatch, guarded_mocks
    ):
        sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None, role="implementation")
        monkeypatch.setattr(
            "issuesmith.engine._run_pre_gate_phase",
            MagicMock(return_value=None),
        )
        monkeypatch.setattr(
            "issuesmith.engine._run_guarded_order",
            MagicMock(return_value=(0, "PIPELINE_STATUS: IMPL_DONE\n")),
        )
        monkeypatch.setattr(
            "issuesmith.ops.dispatch.run_requires_loop",
            MagicMock(side_effect=sig),
        )

        rc = self._call()

        assert rc == 0
        guarded_mocks["handle"].assert_called_once_with(sig, "p1", 5802)

    def test_accepts_post_requires_retry(
        self, monkeypatch, guarded_mocks
    ):
        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="implementation")
        requires_loop = MagicMock(side_effect=[None, sig])
        monkeypatch.setattr(
            "issuesmith.engine._run_pre_gate_phase",
            MagicMock(return_value=None),
        )
        monkeypatch.setattr(
            "issuesmith.engine._run_guarded_order",
            MagicMock(return_value=(0, "PIPELINE_STATUS: IMPL_DONE\n")),
        )
        monkeypatch.setattr(
            "issuesmith.ops.dispatch.run_requires_loop",
            requires_loop,
        )

        rc = self._call()

        assert rc == 0
        assert requires_loop.call_count == 2
        guarded_mocks["handle"].assert_called_once_with(sig, "p1", 5802)
