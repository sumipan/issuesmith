"""Tests for rate limit pause TTL (engines.<role>.pause_ttl_sec, #4987)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import ManagedResult
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.config import _build_engines
from issuesmith.engine import RetryReason, RetrySignal, RoleSelection, _execute

# ---- config ----


def test_pause_ttl_defaults_to_3600():
    engines = _build_engines(None)
    assert {role: cfg.pause_ttl_sec for role, cfg in engines.items()} == {
        "design": 3600,
        "implementation": 3600,
    }


def test_pause_ttl_override():
    engines = _build_engines({"design": {"pause_ttl_sec": 600}})
    assert engines["design"].pause_ttl_sec == 600
    assert engines["implementation"].pause_ttl_sec == 3600


@pytest.mark.parametrize("bad", [0, -1, "600", 1.5, True])
def test_pause_ttl_invalid_raises(bad):
    with pytest.raises(
        ValueError, match=r"engines\.design\.pause_ttl_sec must be a positive integer"
    ):
        _build_engines({"design": {"pause_ttl_sec": bad}})


# ---- engine ----


def _state(status, resume_at=None):
    return EngineQuotaState(
        status=status,
        observed_at=datetime.now(timezone.utc),
        resume_at=resume_at,
        reason=None,
    )


def _snap(engines):
    return QuotaSnapshot(
        engines=engines,
        deferred_tasks={},
        draining_engines={},
        running_tasks={},
    )


def _result(engine="claude", *, rc=0, body="ok"):
    return ManagedResult(
        body=body,
        usage=None,
        returncode=rc,
        failure_class=None,
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
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda body: True)
    cm = MagicMock(return_value=_result())
    monkeypatch.setattr("issuesmith.engine.call_managed", cm)
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    return {"call_managed": cm, "quota_gate": qg}


def _no_fallback(exe_mocks):
    available = _snap({"claude": _state("available"), "codex": _state("available")})
    paused = _snap({"claude": _state("paused"), "codex": _state("paused")})
    exe_mocks["quota_gate"].snapshot.side_effect = [available, paused]
    exe_mocks["call_managed"].return_value = _result("claude", rc=1, body="429")


def _report_kwargs(exe_mocks):
    exe_mocks["quota_gate"].report.assert_called_once()
    return exe_mocks["quota_gate"].report.call_args.kwargs


def test_report_gets_default_resume_at(exe_mocks):
    _no_fallback(exe_mocks)
    with pytest.raises(RetrySignal):
        _execute("design", "prompt")
    kwargs = _report_kwargs(exe_mocks)
    assert kwargs["status"] == "paused"
    assert kwargs["reason"] == "rate_limit_detected"
    assert kwargs["resume_at"] - kwargs["observed_at"] == timedelta(seconds=3600)
    assert kwargs["resume_at"].tzinfo is not None


def test_report_resume_at_uses_role_ttl(exe_mocks, monkeypatch):
    monkeypatch.setattr(
        "issuesmith.engine.DEFAULT_PAUSE_TTL_SEC", {"design": 600, "implementation": 3600}
    )
    _no_fallback(exe_mocks)
    with pytest.raises(RetrySignal):
        _execute("design", "prompt")
    kwargs = _report_kwargs(exe_mocks)
    assert kwargs["resume_at"] - kwargs["observed_at"] == timedelta(seconds=600)


def test_retry_signal_after_equals_resume_at(exe_mocks):
    _no_fallback(exe_mocks)
    with pytest.raises(RetrySignal) as exc_info:
        _execute("design", "prompt")
    assert exc_info.value.reason == RetryReason.RATE_LIMITED
    assert exc_info.value.after == _report_kwargs(exe_mocks)["resume_at"]


def test_fallback_still_used_and_resume_at_reported(exe_mocks):
    available = _snap({"claude": _state("available"), "codex": _state("available")})
    exe_mocks["quota_gate"].snapshot.return_value = available
    exe_mocks["call_managed"].side_effect = [
        _result("claude", rc=1, body="rate limit"),
        _result("codex"),
    ]
    result = _execute("design", "prompt")
    assert result.returncode == 0
    assert exe_mocks["call_managed"].call_args.kwargs["engine"] == "codex"
    kwargs = _report_kwargs(exe_mocks)
    assert kwargs["engine"] == "claude"
    assert kwargs["resume_at"] - kwargs["observed_at"] == timedelta(seconds=3600)
