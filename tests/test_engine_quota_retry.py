"""Tests for _execute() quota retry when all engines are paused."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import ManagedResult
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.engine import (
    _RETRY_WAIT_MAX_SECONDS,
    RoleSelection,
    _execute,
)


def _paused(resume_at: datetime | None, *, reason: str = "quota") -> EngineQuotaState:
    return EngineQuotaState(
        status="paused",
        observed_at=datetime.now(timezone.utc),
        resume_at=resume_at,
        reason=reason,
    )


def _available() -> EngineQuotaState:
    return EngineQuotaState(
        status="available",
        observed_at=datetime.now(timezone.utc),
        resume_at=None,
        reason=None,
    )


def _snapshot(engines: dict[str, EngineQuotaState]) -> QuotaSnapshot:
    return QuotaSnapshot(
        engines=engines,
        deferred_tasks={},
        draining_engines={},
        running_tasks={},
    )


def _success_result(engine: str = "claude") -> ManagedResult:
    return ManagedResult(
        body="ok",
        usage=None,
        returncode=0,
        failure_class=None,
        engine_used=engine,
        model_used="m",
        attempts=1,
        quota_reported=False,
        additional_tags={},
    )


@pytest.fixture
def execute_mocks(monkeypatch):
    """Common mocks for _execute() tests."""
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)

    call_managed = MagicMock(return_value=_success_result())
    monkeypatch.setattr("issuesmith.engine.call_managed", call_managed)
    sleep = MagicMock()
    monkeypatch.setattr("issuesmith.engine.time.sleep", sleep)

    quota_gate = MagicMock()
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate",
        lambda state_path: quota_gate,
    )

    return {
        "call_managed": call_managed,
        "sleep": sleep,
        "quota_gate": quota_gate,
    }


def test_retry_wait_max_seconds_constant():
    assert _RETRY_WAIT_MAX_SECONDS == 1800


def test_all_paused_retries_when_resume_within_window(execute_mocks):
    now = datetime.now(timezone.utc)
    resume_soon = now + timedelta(seconds=30)
    snap_paused = _snapshot(
        {
            "claude": _paused(resume_soon),
            "codex": _paused(now + timedelta(seconds=60)),
        }
    )
    snap_available = _snapshot(
        {
            "claude": _available(),
            "codex": _paused(now + timedelta(seconds=60)),
        }
    )
    execute_mocks["quota_gate"].snapshot.side_effect = [snap_paused, snap_available]

    result = _execute("design", "prompt")

    assert result.returncode == 0
    execute_mocks["sleep"].assert_called_once()
    wait_sec = execute_mocks["sleep"].call_args[0][0]
    assert 25 <= wait_sec <= 35
    execute_mocks["call_managed"].assert_called_once()
    assert execute_mocks["call_managed"].call_args.kwargs["engine"] == "claude"


def test_all_paused_raises_when_resume_beyond_window(execute_mocks):
    now = datetime.now(timezone.utc)
    resume_far = now + timedelta(seconds=_RETRY_WAIT_MAX_SECONDS + 60)
    snap_paused = _snapshot(
        {
            "claude": _paused(resume_far),
            "codex": _paused(resume_far),
        }
    )
    execute_mocks["quota_gate"].snapshot.return_value = snap_paused

    with pytest.raises(RuntimeError, match="All engines paused for role design"):
        _execute("design", "prompt")

    execute_mocks["sleep"].assert_not_called()
    execute_mocks["call_managed"].assert_not_called()


def test_all_paused_raises_when_resume_at_none(execute_mocks):
    snap_paused = _snapshot(
        {
            "claude": _paused(None),
            "codex": _paused(datetime.now(timezone.utc) + timedelta(seconds=30)),
        }
    )
    execute_mocks["quota_gate"].snapshot.return_value = snap_paused

    with pytest.raises(RuntimeError, match="All engines paused for role design"):
        _execute("design", "prompt")

    execute_mocks["sleep"].assert_not_called()
    execute_mocks["call_managed"].assert_not_called()


def test_primary_paused_uses_fallback_without_retry(execute_mocks):
    snap = _snapshot(
        {
            "claude": _paused(datetime.now(timezone.utc) + timedelta(seconds=30)),
            "codex": _available(),
        }
    )
    execute_mocks["quota_gate"].snapshot.return_value = snap
    execute_mocks["call_managed"].return_value = _success_result(engine="codex")

    result = _execute("design", "prompt")

    assert result.returncode == 0
    execute_mocks["sleep"].assert_not_called()
    execute_mocks["quota_gate"].snapshot.assert_called_once()
    execute_mocks["call_managed"].assert_called_once()
    assert execute_mocks["call_managed"].call_args.kwargs["engine"] == "codex"
