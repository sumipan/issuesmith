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
        ("45", "10", 45),  # POLL 優先
        (None, "45", 45),  # 未設定時は INTERVAL 互換
        (None, None, 60),
        ("0", None, 60),
        ("-1", None, 60),
        ("61", None, 60),
        ("abc", None, 60),
        (None, "0", 60),
        (None, "3600", 60),  # 旧既定 3600 も 60 に正規化
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


def test_ac1_polls_until_primary_available(execute_mocks, monkeypatch):
    """AC-1: paused, paused, available → sleep≤60 each, 3 snapshots, 1 call_managed."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    _install_fake_clock(monkeypatch, execute_mocks["sleep"])

    snap_paused = _snapshot(
        {
            "claude": _paused(None, reason="session_5h=96.0"),
            "codex": _paused(None, reason="session_5h=96.0"),
        }
    )
    snap_available = _snapshot(
        {
            "claude": _available(),
            "codex": _available(),
        }
    )
    execute_mocks["quota_gate"].snapshot.side_effect = [
        snap_paused,
        snap_paused,
        snap_available,
    ]

    result = _execute("design", "prompt")

    assert result.returncode == 0
    assert execute_mocks["quota_gate"].snapshot.call_count == 3
    assert execute_mocks["sleep"].call_count == 2
    for call in execute_mocks["sleep"].call_args_list:
        assert call.args[0] <= 60
    execute_mocks["call_managed"].assert_called_once()
    assert execute_mocks["call_managed"].call_args.kwargs["engine"] == "claude"


def test_ac2_resume_at_far_still_polls_at_60s(execute_mocks, monkeypatch):
    """AC-2: resume_at 2h ahead still re-snapshots every ≤60s (4 snaps in 180s)."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    clock = _install_fake_clock(monkeypatch, execute_mocks["sleep"])

    resume_far = datetime.now(timezone.utc) + timedelta(hours=2)
    snaps = [
        _snapshot(
            {
                "claude": _paused(resume_far),
                "codex": _paused(resume_far),
            }
        )
        for _ in range(3)
    ]
    snaps.append(
        _snapshot(
            {
                "claude": _available(),
                "codex": _paused(resume_far),
            }
        )
    )
    execute_mocks["quota_gate"].snapshot.side_effect = snaps

    result = _execute("design", "prompt")

    assert result.returncode == 0
    assert execute_mocks["quota_gate"].snapshot.call_count == 4
    assert execute_mocks["sleep"].call_count == 3
    for call in execute_mocks["sleep"].call_args_list:
        assert call.args[0] <= 60
    assert clock.now == 1000.0 + 180.0


def test_ac3_raises_when_wait_deadline_reached(execute_mocks, monkeypatch):
    """AC-3: WAIT_MAX=120 and forever-paused → RuntimeError, no call_managed."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "120")
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

    assert execute_mocks["sleep"].call_count >= 1
    assert sum(c.args[0] for c in execute_mocks["sleep"].call_args_list) <= 120
    execute_mocks["call_managed"].assert_not_called()


def test_ac4_call_managed_timeout_is_remaining_budget(execute_mocks, monkeypatch):
    """AC-4: 120s wait then resume → timeout ≤ 480, wait not added."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    _install_fake_clock(monkeypatch, execute_mocks["sleep"])

    snap_paused = _snapshot(
        {
            "claude": _paused(None),
            "codex": _paused(None),
        }
    )
    snap_available = _snapshot(
        {
            "claude": _available(),
            "codex": _available(),
        }
    )
    execute_mocks["quota_gate"].snapshot.side_effect = [
        snap_paused,
        snap_paused,
        snap_available,
    ]

    _execute("design", "prompt")

    assert sum(c.args[0] for c in execute_mocks["sleep"].call_args_list) == 120
    timeout = execute_mocks["call_managed"].call_args.kwargs["timeout"]
    assert timeout <= 480
    assert timeout >= 479  # 600 - 120, integer truncation may drop a second


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


def test_ac6_waiting_log_every_300_seconds(execute_mocks, monkeypatch, capsys):
    """AC-6: 610s wait at 60s poll → logs at start, ~300s, ~600s only."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    # wait 610 + 300 reserve ⇒ total timeout ≥ 910
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 1200.0)

    clock = _FakeClock()
    start = clock.now

    def tracked_sleep(seconds: float) -> None:
        clock.advance(float(seconds))

    execute_mocks["sleep"].side_effect = tracked_sleep
    monkeypatch.setattr("issuesmith.engine.time.monotonic", clock.monotonic)

    snap_paused = _snapshot(
        {
            "claude": _paused(None),
            "codex": _paused(None),
        }
    )

    def snapshot_side_effect():
        if clock.now - start >= 610:
            return _snapshot(
                {
                    "claude": _available(),
                    "codex": _available(),
                }
            )
        return snap_paused

    execute_mocks["quota_gate"].snapshot.side_effect = snapshot_side_effect

    _execute("design", "prompt")

    err = capsys.readouterr().err
    waiting_lines = [
        line for line in err.splitlines() if "[issuesmith-engine] waiting" in line
    ]
    assert len(waiting_lines) == 3
    for line in waiting_lines:
        assert "design" in line
        assert "claude" in line or "codex" in line
        assert "elapsed=" in line
        assert "remaining=" in line
    elapsed_vals = [
        int(float(line.split("elapsed=")[1].split("s")[0])) for line in waiting_lines
    ]
    assert elapsed_vals[0] == 0
    assert 280 <= elapsed_vals[1] <= 320
    assert 580 <= elapsed_vals[2] <= 620


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


def test_resume_at_soon_caps_sleep_to_poll(execute_mocks, monkeypatch):
    """Known near resume_at still caps a single sleep to the poll period."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    _install_fake_clock(monkeypatch, execute_mocks["sleep"])

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
    wait_sec = execute_mocks["sleep"].call_args.args[0]
    assert 0 < wait_sec <= 60
    # 待機分を timeout に加算しない（総予算 600 - 待機）
    timeout = execute_mocks["call_managed"].call_args.kwargs["timeout"]
    assert timeout <= 600
    assert timeout == 600 - int(wait_sec) or timeout == int(600 - wait_sec)


def test_due_resume_refetches_immediately_once_with_future_fallback(
    execute_mocks, monkeypatch
):
    """A due primary is re-fetched now even when a fallback resumes later."""
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_POLL_SEC", "60")
    monkeypatch.setenv("ISSUESMITH_ENGINE_WAIT_MAX_SEC", "3600")
    _install_fake_clock(monkeypatch, execute_mocks["sleep"])

    now = datetime.now(timezone.utc)
    snap_paused = _snapshot(
        {
            "claude": _paused(now - timedelta(seconds=1)),
            "codex": _paused(now + timedelta(hours=2)),
        }
    )
    snap_available = _snapshot(
        {
            "claude": _available(),
            "codex": _paused(now + timedelta(hours=2)),
        }
    )
    execute_mocks["quota_gate"].snapshot.side_effect = [
        snap_paused,
        snap_paused,
        snap_available,
    ]

    result = _execute("design", "prompt")

    assert result.returncode == 0
    execute_mocks["sleep"].assert_called_once_with(60.0)
    assert execute_mocks["quota_gate"].snapshot.call_count == 3
