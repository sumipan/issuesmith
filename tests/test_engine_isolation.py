"""Tests for exec_file / _execute isolation passthrough to call_managed (#5542)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import ManagedResult
from ghdag.metrics.models import FailureClass
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.engine import (
    _LAST_LLM_STDERR,
    RoleSelection,
    _execute,
    exec_file,
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
        limits={},
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
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    snap = _snap({"claude": _available(), "codex": _available()})
    qg.snapshot.return_value = snap
    return {"call_managed": cm, "quota_gate": qg}


def test_exec_file_forwards_isolation_to_execute(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    def _fake_execute(*_args, **kwargs):
        captured.update(kwargs)
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("issuesmith.engine._execute", _fake_execute)
    order = tmp_path / "order.md"
    order.write_text("prompt", encoding="utf-8")
    assert exec_file("design", str(order), isolation=True) == 0
    assert captured.get("isolation") is True


def test_call_managed_receives_isolation_true_on_success(exe_mocks):
    _execute("design", "prompt", isolation=True)
    assert exe_mocks["call_managed"].call_args.kwargs["isolation"] is True


def test_call_managed_receives_isolation_none_when_omitted(exe_mocks):
    _execute("design", "prompt")
    assert exe_mocks["call_managed"].call_args.kwargs["isolation"] is None


def test_rate_limit_fallback_preserves_isolation_true(exe_mocks, monkeypatch):
    rate_limited = _result("claude", rc=1, body="fail")
    exe_mocks["call_managed"].side_effect = [rate_limited, _result("codex")]
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda _text: True)
    monkeypatch.setattr(
        "issuesmith.engine._rate_limit_evidence", lambda _text: "rate limit"
    )

    _execute("design", "prompt", isolation=True)

    calls = exe_mocks["call_managed"].call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["isolation"] is True
    assert calls[1].kwargs["isolation"] is True


def test_env_error_fallback_preserves_isolation_false(exe_mocks):
    env_error = _result(
        "claude", rc=1, failure_class=FailureClass.ENGINE_ENVIRONMENT_ERROR.value
    )
    exe_mocks["call_managed"].side_effect = [env_error, _result("codex")]

    _execute("design", "prompt", isolation=False)

    calls = exe_mocks["call_managed"].call_args_list
    assert len(calls) == 2
    assert calls[0].kwargs["isolation"] is False
    assert calls[1].kwargs["isolation"] is False


def test_existing_call_managed_kwargs_unchanged_with_isolation(exe_mocks, tmp_path):
    cwd = tmp_path / "wt"
    cwd.mkdir()
    _execute("design", "prompt", cwd=str(cwd), isolation=True)
    kwargs = exe_mocks["call_managed"].call_args.kwargs
    assert kwargs["engine"] == "claude"
    assert kwargs["model"] == "m"
    assert kwargs["capabilities"] is not None
    assert kwargs["quota_gate"] is exe_mocks["quota_gate"]
    assert kwargs["cwd"] == cwd.resolve()
    assert isinstance(kwargs["fallback_candidates"], list)
    assert kwargs["isolation"] is True


def test_rate_limit_fallback_uses_stderr_for_detection(exe_mocks):
    """Ensure rate-limit path still runs when isolation is set (stderr evidence)."""
    stderr = "Error: 429 Too Many Requests"
    calls = {"n": 0}

    def _side_effect(*_args, **_kwargs):
        if calls["n"] == 0:
            calls["n"] += 1
            _LAST_LLM_STDERR[0] = stderr
            return _result("claude", rc=1, body="")
        return _result("codex")

    exe_mocks["call_managed"].side_effect = _side_effect
    result = _execute("design", "prompt", isolation=True)
    assert result.returncode == 0
    assert exe_mocks["call_managed"].call_count == 2
