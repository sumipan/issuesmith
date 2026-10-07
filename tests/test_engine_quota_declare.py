"""Tests for _execute() declaring LLM runs to the ghdag QuotaGate (#4789)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call

import pytest
from ghdag.llm import ManagedResult
from ghdag.quota import AdmissionDecision, EngineQuotaState, QuotaSnapshot

from issuesmith.engine import (
    RetryReason,
    RetrySignal,
    RoleSelection,
    _execute,
)

ENGINES = ["claude", "cursor", "codex"]
ROLE = "implementation"
TASK_UUID = "uuid-1"


def _available() -> EngineQuotaState:
    return EngineQuotaState(
        status="available",
        observed_at=datetime.now(timezone.utc),
        resume_at=None,
        reason=None,
    )


def _snapshot() -> QuotaSnapshot:
    return QuotaSnapshot(
        engines={engine: _available() for engine in ENGINES},
        deferred_tasks={},
        draining_engines={},
        running_tasks={},
    )


def _result(
    engine: str = "claude", *, returncode: int = 0, body: str = "ok"
) -> ManagedResult:
    return ManagedResult(
        body=body,
        usage=None,
        returncode=returncode,
        failure_class=None,
        engine_used=engine,
        model_used="m",
        attempts=1,
        quota_reported=False,
        additional_tags={},
    )


def _allowed() -> AdmissionDecision:
    return AdmissionDecision(
        allowed=True, status="RUNNING", reason=None, resume_at=None
    )


@pytest.fixture
def mocks(monkeypatch, tmp_path):
    """Shared parent mock holding the gate and call_managed for call ordering."""
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", tmp_path / "quota.json")
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", tmp_path / "quota.json")
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    monkeypatch.setattr("issuesmith.engine.time.sleep", MagicMock())

    parent = MagicMock()
    gate = parent.gate
    gate.snapshot.return_value = _snapshot()
    gate.begin_run.return_value = _allowed()
    gate.finish_run.return_value = True
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: gate)

    call_managed = parent.call_managed
    call_managed.return_value = _result()
    monkeypatch.setattr("issuesmith.engine.call_managed", call_managed)

    monkeypatch.setenv("GHDAG_TASK_UUID", TASK_UUID)
    return parent


def _use_engine(monkeypatch, engine: str) -> None:
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine=engine, model="m"),
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_begin_run_declares_selected_engine_before_call_managed(
    mocks, monkeypatch, engine
):
    _use_engine(monkeypatch, engine)
    mocks.call_managed.return_value = _result(engine)

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    mocks.gate.begin_run.assert_called_once_with(task_uuid=TASK_UUID, engine=engine)
    kwargs = mocks.gate.begin_run.call_args.kwargs
    assert "role" not in kwargs
    assert "role_engines" not in kwargs
    assert kwargs["engine"] != "shell"
    names = [c[0] for c in mocks.mock_calls]
    assert names.index("gate.begin_run") < names.index("call_managed")


@pytest.mark.parametrize("engine", ENGINES)
def test_begin_run_denied_raises_quota_paused(mocks, monkeypatch, engine):
    _use_engine(monkeypatch, engine)
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    mocks.gate.begin_run.return_value = AdmissionDecision(
        allowed=False, status="DEFERRED", reason="drain", resume_at=resume_at
    )

    with pytest.raises(RetrySignal) as exc_info:
        _execute(ROLE, "prompt")

    assert exc_info.value.reason == RetryReason.QUOTA_PAUSED
    assert exc_info.value.after == resume_at
    assert exc_info.value.role == ROLE
    mocks.call_managed.assert_not_called()
    mocks.gate.finish_run.assert_not_called()


def test_call_order_begin_call_finish(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")

    _execute(ROLE, "prompt")

    relevant = [
        c
        for c in mocks.mock_calls
        if c[0] in ("gate.begin_run", "call_managed", "gate.finish_run")
    ]
    assert [c[0] for c in relevant] == [
        "gate.begin_run",
        "call_managed",
        "gate.finish_run",
    ]
    assert relevant[0] == call.gate.begin_run(task_uuid=TASK_UUID, engine="claude")
    assert relevant[2] == call.gate.finish_run(task_uuid=TASK_UUID)


def test_finish_run_called_when_call_managed_raises(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    mocks.call_managed.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _execute(ROLE, "prompt")

    mocks.gate.finish_run.assert_called_once_with(task_uuid=TASK_UUID)


def test_finish_run_called_on_timeout(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    mocks.call_managed.side_effect = subprocess.TimeoutExpired(cmd="llm", timeout=600)

    result = _execute(ROLE, "prompt")

    assert result.returncode == 124
    mocks.gate.finish_run.assert_called_once_with(task_uuid=TASK_UUID)


def test_rate_limit_retry_redeclares_alt_engine(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda text: True)
    mocks.call_managed.side_effect = [
        _result("claude", returncode=1, body="rate limit exceeded"),
        _result("codex"),
    ]

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    retry_engine = mocks.call_managed.call_args_list[1].kwargs["engine"]
    assert retry_engine != "claude"
    assert mocks.gate.begin_run.call_args_list == [
        call(task_uuid=TASK_UUID, engine="claude"),
        call(task_uuid=TASK_UUID, engine=retry_engine),
    ]
    names = [c[0] for c in mocks.mock_calls]
    begin_idx = [i for i, n in enumerate(names) if n == "gate.begin_run"]
    call_idx = [i for i, n in enumerate(names) if n == "call_managed"]
    assert begin_idx[1] < call_idx[1]
    mocks.gate.finish_run.assert_called_once_with(task_uuid=TASK_UUID)


def test_rate_limit_retry_denied_raises_quota_paused(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda text: True)
    mocks.call_managed.side_effect = [
        _result("claude", returncode=1, body="rate limit exceeded"),
        _result("codex"),
    ]
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    mocks.gate.begin_run.side_effect = [
        _allowed(),
        AdmissionDecision(
            allowed=False, status="DEFERRED", reason="drain", resume_at=resume_at
        ),
    ]

    with pytest.raises(RetrySignal) as exc_info:
        _execute(ROLE, "prompt")

    assert exc_info.value.reason == RetryReason.QUOTA_PAUSED
    assert exc_info.value.after == resume_at
    assert mocks.call_managed.call_count == 1
    mocks.gate.finish_run.assert_called_once_with(task_uuid=TASK_UUID)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_no_task_uuid_skips_declaration(mocks, monkeypatch, value):
    _use_engine(monkeypatch, "claude")
    if value is None:
        monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
    else:
        monkeypatch.setenv("GHDAG_TASK_UUID", value)

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    assert result.stdout == "ok"
    mocks.call_managed.assert_called_once()
    mocks.gate.begin_run.assert_not_called()
    mocks.gate.finish_run.assert_not_called()


def test_begin_run_oserror_is_logged_and_ignored(mocks, monkeypatch, capsys):
    _use_engine(monkeypatch, "claude")
    mocks.gate.begin_run.side_effect = OSError("disk full")

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    mocks.call_managed.assert_called_once()
    assert "quota declare failed" in capsys.readouterr().err


def test_finish_run_valueerror_is_logged_and_ignored(mocks, monkeypatch, capsys):
    _use_engine(monkeypatch, "claude")
    mocks.gate.finish_run.side_effect = ValueError("bad state")

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    assert "quota declare failed" in capsys.readouterr().err
