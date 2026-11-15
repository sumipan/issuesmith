"""Tests for _execute() quota wait polling when all engines are paused."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.quota import AdmissionDecision, EngineQuotaState, QuotaSnapshot

from issuesmith.engine import (
    _RETRY_INTERVAL_SEC_DEFAULT,
    _RETRY_WAIT_MAX_SECONDS,
    _WAIT_MAX_SEC_DEFAULT,
    RoleSelection,
    _execute,
    _wait_poll_sec,
)


def _paused(resume_at: datetime | None, *, reason: str='quota') -> EngineQuotaState:
    return EngineQuotaState(status='paused', observed_at=datetime.now(timezone.utc), resume_at=resume_at, reason=reason)

def _available() -> EngineQuotaState:
    return EngineQuotaState(status='available', observed_at=datetime.now(timezone.utc), resume_at=None, reason=None)

def _snapshot(engines: dict[str, EngineQuotaState]) -> QuotaSnapshot:
    return QuotaSnapshot(engines=engines, draining_engines={}, running_tasks={}, limits={})

def _success_result(engine: str='claude') -> LLMResult:
    return LLMResult(stdout='', stderr='', body='ok', usage=None, returncode=0, failure_class=None, engine_used=engine, model_used='m', attempts=1, quota_reported=False, additional_tags={})

class _FakeClock:
    """Controllable monotonic clock for wait-loop tests."""

    def __init__(self, start: float=1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

class RecordingQuotaGate:

    def __init__(self) -> None:
        self.snapshot_result = None
        self.snapshot_side_effect = None
        self.snapshot_calls = 0
        self.reports: list[dict] = []

    def snapshot(self):
        self.snapshot_calls += 1
        if self.snapshot_side_effect is not None:
            return self.snapshot_side_effect()
        return self.snapshot_result

    def report(self, **kwargs):
        self.reports.append(dict(kwargs))

    def begin_run(self, **kwargs):
        return AdmissionDecision(allowed=True, status='RUNNING', reason=None, resume_at=None)

    def finish_run(self, **kwargs):
        return True

class RecordingCallManaged:

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.return_value = None
        self.side_effect = None

    def __call__(self, *args, **kwargs):
        record = dict(kwargs)
        if args:
            record['content'] = args[0]
        self.calls.append(record)
        if self.side_effect is not None:
            if isinstance(self.side_effect, list):
                item = self.side_effect.pop(0)
                return item
            return self.side_effect
        return self.return_value

@pytest.fixture
def execute_mocks(monkeypatch):
    """Common mocks for _execute() tests."""
    monkeypatch.setattr('issuesmith.engine.resolve', lambda role, tier=None: RoleSelection(engine='claude', model='m'))
    monkeypatch.setattr('issuesmith.engine._record_task_metrics', lambda **kwargs: None)
    monkeypatch.setattr('issuesmith.engine._resolve_timeout_sec', lambda role: 600.0)
    call = RecordingCallManaged()
    call.return_value = _success_result()
    monkeypatch.setattr('issuesmith.engine.call', call)
    sleep = MagicMock()
    monkeypatch.setattr('issuesmith.engine.time.sleep', sleep)
    quota_gate = RecordingQuotaGate()
    monkeypatch.setattr('issuesmith.engine.QuotaGate', lambda state_path: quota_gate)
    return {'call': call, 'sleep': sleep, 'quota_gate': quota_gate}

def _install_fake_clock(monkeypatch, sleep_mock: MagicMock) -> _FakeClock:
    clock = _FakeClock()

    def fake_sleep(seconds: float) -> None:
        clock.advance(float(seconds))
    sleep_mock.side_effect = fake_sleep
    monkeypatch.setattr('issuesmith.engine.time.monotonic', clock.monotonic)
    return clock

def test_retry_wait_max_seconds_constant():
    assert _RETRY_WAIT_MAX_SECONDS == 1800
    assert _WAIT_MAX_SEC_DEFAULT == 21600
    assert _RETRY_INTERVAL_SEC_DEFAULT == 3600

@pytest.mark.parametrize(('poll', 'interval', 'expected'), [('30', None, 30), ('45', '10', 45), (None, '45', 45), (None, None, 60), ('0', None, 60), ('-1', None, 60), ('61', None, 60), ('abc', None, 60), (None, '0', 60), (None, '3600', 60), ('1', None, 1), ('60', None, 60)])
def test_wait_poll_sec_normalization(monkeypatch, poll, interval, expected):
    monkeypatch.delenv('ISSUESMITH_ENGINE_WAIT_POLL_SEC', raising=False)
    monkeypatch.delenv('ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC', raising=False)
    if poll is not None:
        monkeypatch.setenv('ISSUESMITH_ENGINE_WAIT_POLL_SEC', poll)
    if interval is not None:
        monkeypatch.setenv('ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC', interval)
    assert _wait_poll_sec() == expected

def test_call_managed_receives_quota_gate_not_brake(execute_mocks, monkeypatch, tmp_path):
    """AC-7: call always receives the global quota_gate."""
    quota_path = tmp_path / 'quota.json'
    brake_path = tmp_path / 'brake.json'
    monkeypatch.setattr('issuesmith.engine.QUOTA_STATE_PATH', quota_path)
    monkeypatch.setattr('issuesmith.engine.BRAKE_STATE_PATH', brake_path)
    quota_gate = RecordingQuotaGate()
    brake_gate = RecordingQuotaGate()
    gates = {str(quota_path): quota_gate, str(brake_path): brake_gate}
    monkeypatch.setattr('issuesmith.engine.QuotaGate', lambda state_path: gates[str(state_path)])
    available = _snapshot({'claude': _available(), 'codex': _available()})
    quota_gate.snapshot_result = available
    brake_gate.snapshot_result = available
    monkeypatch.setattr('issuesmith.engine.call', execute_mocks['call'])
    _execute('design', 'prompt')
    assert execute_mocks['call'].calls[-1]['quota_gate'] is quota_gate
    assert execute_mocks['call'].calls[-1]['quota_gate'] is not brake_gate

def test_run_verified_emit_propagates_quota_retry(execute_mocks, monkeypatch, tmp_path):
    from ghdag.metrics import FailureClass

    from issuesmith.engine import RetrySignal, run_verified
    execute_mocks['call'].return_value = LLMResult(stdout='', stderr='', body='', usage=None, returncode=1, failure_class=FailureClass.QUOTA_EXHAUSTED, engine_used='codex', model_used='m', attempts=1, quota_reported=True, additional_tags={})
    snap = _snapshot({'claude': _available(), 'codex': _available()})
    execute_mocks['quota_gate'].snapshot_result = snap
    template = tmp_path / 'order.md'
    template.write_text('prompt', encoding='utf-8')
    with pytest.raises(RetrySignal):
        run_verified('design', str(template), [], ['PASS'], 'FAIL', 'true', str(template), cwd=str(tmp_path), emit_status='PASS')
