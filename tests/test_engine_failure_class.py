"""Tests for failure diagnostics in engine._execute (#3611, #4239)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.metrics import FailureClass
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.contract import RetryReason, RetrySignal
from issuesmith.engine import (
    RoleSelection,
    _execute,
    _failure_class_from_value,
    _truncate_failure_stderr,
)


@pytest.mark.parametrize("member", list(FailureClass))
def test_known_value_returns_member(member):
    assert _failure_class_from_value(member.value) is member


def test_unknown_value_returns_none():
    assert _failure_class_from_value("no-such-class") is None


def test_truncate_failure_stderr_keeps_tail_lines():
    lines = [f"line-{index}" for index in range(50)]
    stderr = "\n".join(lines)
    truncated = _truncate_failure_stderr(stderr)
    kept = truncated.splitlines()
    assert len(kept) == 40
    assert kept[0] == "line-10"
    assert kept[-1] == "line-49"


def test_truncate_failure_stderr_keeps_tail_bytes():
    stderr = "x" * 5000
    truncated = _truncate_failure_stderr(stderr)
    assert len(truncated.encode("utf-8")) <= 4096
    assert truncated == "x" * 4096


def _available() -> EngineQuotaState:
    from datetime import datetime, timezone

    return EngineQuotaState(
        status="available",
        observed_at=datetime.now(timezone.utc),
        resume_at=None,
        reason=None,
    )


def _snapshot() -> QuotaSnapshot:
    return QuotaSnapshot(
        engines={"claude": _available(), "cursor": _available(), "codex": _available()},
        draining_engines={},
        running_tasks={},
        limits={},
    )


@pytest.fixture
def execute_mocks(monkeypatch):
    recorded: list[dict] = []

    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="claude-sonnet-4-6"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: recorded.append(kwargs))
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    quota_gate = MagicMock()
    quota_gate.snapshot.return_value = _snapshot()
    quota_gate.increment_auth_strike.return_value = 1
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: quota_gate)
    return {"recorded": recorded}


def test_execute_failure_includes_truncated_stderr(execute_mocks, monkeypatch):
    monkeypatch.setattr(
        "issuesmith.engine.call",
        lambda prompt, **kwargs: LLMResult(
            body="",
            stdout="",
            stderr="API error: stream disconnected",
            returncode=1,
            engine_used="claude",
            model_used="m",
        ),
    )

    proc = _execute("implementation", "prompt")

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == "API error: stream disconnected"
    assert execute_mocks["recorded"][-1]["failure_detail"] == "API error: stream disconnected"


def test_execute_failure_records_the_failure_class_of_the_result(execute_mocks, monkeypatch):
    monkeypatch.setattr(
        "issuesmith.engine.ROLE_ENGINES",
        {"implementation": ("claude",)},
    )
    monkeypatch.setattr(
        "issuesmith.engine.call",
        lambda prompt, **kwargs: LLMResult(
            body="",
            stdout="",
            stderr="Error: authentication_error: invalid api key",
            returncode=1,
            failure_class=FailureClass.AUTH,
            engine_used="claude",
            model_used="m",
        ),
    )

    proc = _execute("implementation", "prompt")

    assert proc.returncode == 1
    assert proc.stderr
    assert execute_mocks["recorded"][-1]["failure_class"] == FailureClass.AUTH.value


def test_execute_success_does_not_persist_stderr(execute_mocks, monkeypatch):
    monkeypatch.setattr(
        "issuesmith.engine.call",
        lambda prompt, **kwargs: LLMResult(
            body="done",
            stdout="done",
            stderr="debug noise",
            returncode=0,
            engine_used="claude",
            model_used="m",
        ),
    )

    proc = _execute("implementation", "prompt")

    assert proc.returncode == 0
    assert proc.stdout == "done"
    assert proc.stderr == ""
    assert execute_mocks["recorded"][-1].get("failure_detail") is None


def test_execute_timeout_expired_records_metrics_and_returns_124(execute_mocks, monkeypatch):
    def raise_timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["agent"], timeout=3599)

    monkeypatch.setattr("issuesmith.engine.call", raise_timeout)

    proc = _execute("implementation", "prompt")

    assert proc.returncode == 124
    assert proc.stdout == ""
    assert "TimeoutExpired after 3599s" in proc.stderr
    recorded = execute_mocks["recorded"][-1]
    assert recorded["status"] == "timeout"
    assert recorded["failure_class"] == FailureClass.TIMEOUT.value
    assert recorded["failure_detail"] == "TimeoutExpired after 3599s"


def test_execute_llm_result_timeout_records_metrics(execute_mocks, monkeypatch):
    from ghdag.llm import LLMResult

    monkeypatch.setattr(
        "issuesmith.engine.call",
        lambda *args, **kwargs: LLMResult(stdout="", stderr="",
            body="",
            usage=None,
            returncode=124,
            failure_class=FailureClass.TIMEOUT,
            engine_used="claude",
            model_used="claude-sonnet-4-6",
            attempts=1,
            quota_reported=False,
            additional_tags={},
        ),
    )

    proc = _execute("implementation", "prompt")

    assert proc.returncode == 124
    recorded = execute_mocks["recorded"][-1]
    assert recorded["status"] == "timeout"
    assert recorded["failure_class"] == FailureClass.TIMEOUT.value


def test_env_error_from_call_raises_without_retry(execute_mocks, monkeypatch):
    """Refs sumipan/nexus#6681 — ENGINE_ENVIRONMENT_ERROR does not issue a second call."""
    from ghdag.llm import LLMResult

    monkeypatch.setattr(
        "issuesmith.engine.call",
        lambda prompt, **kwargs: LLMResult(
            body="",
            stdout="",
            stderr="",
            usage=None,
            returncode=1,
            failure_class=FailureClass.ENGINE_ENVIRONMENT_ERROR,
            engine_used="claude",
            model_used="m",
            attempts=1,
            quota_reported=False,
            additional_tags={},
        ),
    )

    with pytest.raises(RetrySignal) as exc_info:
        _execute("implementation", "prompt")

    assert exc_info.value.reason == RetryReason.ENGINE_ENVIRONMENT_ERROR
