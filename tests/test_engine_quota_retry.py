"""Tests for _execute() quota wait polling when all engines are paused."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import ManagedResult
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.engine import (
    _RETRY_INTERVAL_SEC_DEFAULT,
    _RETRY_WAIT_MAX_SECONDS,
    _WAIT_MAX_SEC_DEFAULT,
    RoleSelection,
    _execute,
    _wait_poll_sec,
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


class _FakeClock:
    """Controllable monotonic clock for wait-loop tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def execute_mocks(monkeypatch):
    """Common mocks for _execute() tests."""
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)

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


def _install_fake_clock(monkeypatch, sleep_mock: MagicMock) -> _FakeClock:
    clock = _FakeClock()

    def fake_sleep(seconds: float) -> None:
        clock.advance(float(seconds))

    sleep_mock.side_effect = fake_sleep
    monkeypatch.setattr("issuesmith.engine.time.monotonic", clock.monotonic)
    return clock


def test_retry_wait_max_seconds_constant():
    assert _RETRY_WAIT_MAX_SECONDS == 1800
    assert _WAIT_MAX_SEC_DEFAULT == 21600
    assert _RETRY_INTERVAL_SEC_DEFAULT == 3600


@pytest.mark.parametrize(
    ("poll", "interval", "expected"),
    [
        ("30", None, 30),
        ("45", "10", 45),  # POLL takes precedence
        (None, "45", 45),  # when unset, INTERVAL is compatible
        (None, None, 60),
        ("0", None, 60),
        ("-1", None, 60),
        ("61", None, 60),
        ("abc", None, 60),
        (None, "0", 60),
        (None, "3600", 60),  # legacy default 3600 also normalized to 60
        ("1", None, 1),
        ("60", None, 60),
    ],
)
def test_wait_poll_sec_normalization(monkeypatch, poll, interval, expected):
    monkeypatch.delenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", raising=False)
    monkeypatch.delenv("ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC", raising=False)
    if poll is not None:
        monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", poll)
    if interval is not None:
        monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC", interval)
    assert _wait_poll_sec() == expected



def test_ac5_timeout_le_300_fails_without_sleep(execute_mocks, monkeypatch):
    """AC-5: total timeout ≤ 300 and all paused → no sleep, no subprocess."""
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 300.0)
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    _install_fake_clock(monkeypatch, execute_mocks["sleep"])

    snap_paused = _snapshot(
        {
            "claude": _paused(None),
            "codex": _paused(None),
        }
    )
    execute_mocks["quota_gate"].snapshot.return_value = snap_paused

    with pytest.raises(RuntimeError, match="All engines paused for role design"):
        _execute("design", "prompt")

    execute_mocks["sleep"].assert_not_called()
    execute_mocks["call_managed"].assert_not_called()



def test_ac8_primary_paused_uses_fallback_without_sleep(execute_mocks):
    """AC-8: primary paused, fallback available → no sleep, one call_managed."""
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



def test_ac6_brake_only_paused_uses_fallback(execute_mocks, monkeypatch, tmp_path):
    """AC-6: primary paused on brake only; fallback available on both gates → use fallback."""
    quota_path = tmp_path / "quota.json"
    brake_path = tmp_path / "brake.json"
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", brake_path)

    quota_gate = MagicMock()
    brake_gate = MagicMock()
    gates = {str(quota_path): quota_gate, str(brake_path): brake_gate}
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate",
        lambda state_path: gates[str(state_path)],
    )

    quota_gate.snapshot.return_value = _snapshot(
        {"claude": _available(), "codex": _available()}
    )
    brake_gate.snapshot.return_value = _snapshot(
        {"claude": _paused(None), "codex": _available()}
    )
    execute_mocks["call_managed"].return_value = _success_result(engine="codex")
    monkeypatch.setattr(
        "issuesmith.engine.call_managed", execute_mocks["call_managed"]
    )

    result = _execute("design", "prompt")

    assert result.returncode == 0
    execute_mocks["sleep"].assert_not_called()
    assert execute_mocks["call_managed"].call_args.kwargs["engine"] == "codex"
    assert execute_mocks["call_managed"].call_args.kwargs["quota_gate"] is quota_gate
    brake_gate.report.assert_not_called()


def test_ac6_all_paused_across_gates_raises(execute_mocks, monkeypatch, tmp_path):
    """AC-6: all candidates paused on some gate → fail under existing timeout contract."""
    quota_path = tmp_path / "quota.json"
    brake_path = tmp_path / "brake.json"
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", brake_path)
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "120")
    _install_fake_clock(monkeypatch, execute_mocks["sleep"])

    quota_gate = MagicMock()
    brake_gate = MagicMock()
    gates = {str(quota_path): quota_gate, str(brake_path): brake_gate}
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate",
        lambda state_path: gates[str(state_path)],
    )

    # claude paused on quota, codex paused on brake → both unavailable
    quota_gate.snapshot.return_value = _snapshot(
        {"claude": _paused(None), "codex": _available()}
    )
    brake_gate.snapshot.return_value = _snapshot(
        {"claude": _available(), "codex": _paused(None)}
    )

    with pytest.raises(RuntimeError, match="All engines paused for role design"):
        _execute("design", "prompt")

    execute_mocks["call_managed"].assert_not_called()


def test_ac7_rate_limit_report_writes_quota_gate_only(
    execute_mocks, monkeypatch, tmp_path
):
    """AC-7: rate_limit_detected updates only the quota gate; does not touch brake."""
    quota_path = tmp_path / "quota.json"
    brake_path = tmp_path / "brake.json"
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", brake_path)

    quota_gate = MagicMock()
    brake_gate = MagicMock()
    gates = {str(quota_path): quota_gate, str(brake_path): brake_gate}
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate",
        lambda state_path: gates[str(state_path)],
    )

    available = _snapshot({"claude": _available(), "codex": _available()})
    quota_gate.snapshot.return_value = available
    brake_gate.snapshot.return_value = available

    rate_limited = ManagedResult(
        body="rate limit exceeded, please try again later",
        usage=None,
        returncode=1,
        failure_class=None,
        engine_used="claude",
        model_used="m",
        attempts=1,
        quota_reported=False,
        additional_tags={},
    )
    execute_mocks["call_managed"].side_effect = [
        rate_limited,
        _success_result(engine="codex"),
    ]
    monkeypatch.setattr(
        "issuesmith.engine.call_managed", execute_mocks["call_managed"]
    )
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda body: True)

    result = _execute("design", "prompt")

    assert result.returncode == 0
    quota_gate.report.assert_called_once()
    assert quota_gate.report.call_args.kwargs["reason"] == "rate_limit_detected"
    brake_gate.report.assert_not_called()
    assert (
        execute_mocks["call_managed"].call_args_list[0].kwargs["quota_gate"]
        is quota_gate
    )
    assert (
        execute_mocks["call_managed"].call_args_list[1].kwargs["quota_gate"]
        is quota_gate
    )


def test_ac7_call_managed_receives_quota_gate_not_brake(
    execute_mocks, monkeypatch, tmp_path
):
    """AC-7: call_managed always receives the global quota_gate."""
    quota_path = tmp_path / "quota.json"
    brake_path = tmp_path / "brake.json"
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", brake_path)

    quota_gate = MagicMock()
    brake_gate = MagicMock()
    gates = {str(quota_path): quota_gate, str(brake_path): brake_gate}
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate",
        lambda state_path: gates[str(state_path)],
    )
    available = _snapshot({"claude": _available(), "codex": _available()})
    quota_gate.snapshot.return_value = available
    brake_gate.snapshot.return_value = available
    monkeypatch.setattr(
        "issuesmith.engine.call_managed", execute_mocks["call_managed"]
    )

    _execute("design", "prompt")

    assert execute_mocks["call_managed"].call_args.kwargs["quota_gate"] is quota_gate
    assert execute_mocks["call_managed"].call_args.kwargs["quota_gate"] is not brake_gate


def test_brake_paused_engine_excluded_from_managed_fallbacks(
    execute_mocks, monkeypatch, tmp_path
):
    """Engines paused on the budget gate are not passed as call_managed fallbacks."""
    quota_path = tmp_path / "quota.json"
    brake_path = tmp_path / "brake.json"
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", brake_path)

    quota_gate = MagicMock()
    brake_gate = MagicMock()
    gates = {str(quota_path): quota_gate, str(brake_path): brake_gate}
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate",
        lambda state_path: gates[str(state_path)],
    )
    quota_gate.snapshot.return_value = _snapshot(
        {"claude": _available(), "codex": _available()}
    )
    brake_gate.snapshot.return_value = _snapshot(
        {"claude": _available(), "codex": _paused(None)}
    )
    monkeypatch.setattr(
        "issuesmith.engine.call_managed", execute_mocks["call_managed"]
    )

    _execute("design", "prompt")

    assert execute_mocks["call_managed"].call_args.kwargs["engine"] == "claude"
    assert execute_mocks["call_managed"].call_args.kwargs["fallback_candidates"] == []
