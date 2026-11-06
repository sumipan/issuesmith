"""Tests for _execute() declaring LLM runs to the ghdag QuotaGate (#4789)."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone

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
        draining_engines={},
        running_tasks={},
        limits={},
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


class RecordingQuotaGate:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.begin_runs: list[dict] = []
        self.finish_runs: list[dict] = []
        self.snapshot_result = _snapshot()
        self.begin_run_result = _allowed()
        self.begin_run_side_effect = None
        self.finish_run_side_effect = None

    def snapshot(self) -> QuotaSnapshot:
        return self.snapshot_result

    def begin_run(self, **kwargs):
        if self.events is not None:
            self.events.append("begin_run")
        self.begin_runs.append(dict(kwargs))
        if self.begin_run_side_effect is not None:
            if isinstance(self.begin_run_side_effect, list):
                return self.begin_run_side_effect.pop(0)
            raise self.begin_run_side_effect
        return self.begin_run_result

    def finish_run(self, **kwargs):
        if self.events is not None:
            self.events.append("finish_run")
        self.finish_runs.append(dict(kwargs))
        if self.finish_run_side_effect is not None:
            raise self.finish_run_side_effect
        return True

    def report(self, **kwargs):
        return None


class RecordingCallManaged:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events
        self.calls: list[dict] = []
        self.return_value = _result()
        self.side_effect = None

    def __call__(self, *args, **kwargs):
        if self.events is not None:
            self.events.append("call_managed")
        record = dict(kwargs)
        if args:
            record["content"] = args[0]
        self.calls.append(record)
        if self.side_effect is not None:
            if isinstance(self.side_effect, list):
                item = self.side_effect.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            if isinstance(self.side_effect, BaseException):
                raise self.side_effect
            return self.side_effect
        return self.return_value


@pytest.fixture
def mocks(monkeypatch, tmp_path):
    monkeypatch.setattr("issuesmith.engine.QUOTA_STATE_PATH", tmp_path / "quota.json")
    monkeypatch.setattr("issuesmith.engine.BRAKE_STATE_PATH", tmp_path / "quota.json")
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    monkeypatch.setattr("issuesmith.engine.time.sleep", lambda *a, **k: None)

    events: list[str] = []
    gate = RecordingQuotaGate(events=events)
    call_managed = RecordingCallManaged(events=events)
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: gate)
    monkeypatch.setattr("issuesmith.engine.call_managed", call_managed)

    monkeypatch.setenv("GHDAG_TASK_UUID", TASK_UUID)
    return {"gate": gate, "call_managed": call_managed, "events": events}


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
    mocks["call_managed"].return_value = _result(engine)

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    gate = mocks["gate"]
    assert len(gate.begin_runs) == 1
    assert gate.begin_runs[0] == {"task_uuid": TASK_UUID, "engine": engine}
    assert "role" not in gate.begin_runs[0]
    assert "role_engines" not in gate.begin_runs[0]
    assert gate.begin_runs[0]["engine"] != "shell"
    assert len(mocks["call_managed"].calls) == 1
    assert gate.begin_runs[0]["engine"] == mocks["call_managed"].calls[0].get("engine", engine)
    assert mocks["events"].index("begin_run") < mocks["events"].index("call_managed")


@pytest.mark.parametrize("engine", ENGINES)
def test_begin_run_denied_raises_quota_paused(mocks, monkeypatch, engine):
    _use_engine(monkeypatch, engine)
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    mocks["gate"].begin_run_result = AdmissionDecision(
        allowed=False, status="DEFERRED", reason="drain", resume_at=resume_at
    )

    with pytest.raises(RetrySignal) as exc_info:
        _execute(ROLE, "prompt")

    assert exc_info.value.reason == RetryReason.QUOTA_PAUSED
    assert exc_info.value.after == resume_at
    assert exc_info.value.role == ROLE
    assert mocks["call_managed"].calls == []
    assert mocks["gate"].finish_runs == []


def test_call_order_begin_call_finish(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")

    _execute(ROLE, "prompt")

    gate = mocks["gate"]
    assert len(gate.begin_runs) == 1
    assert len(mocks["call_managed"].calls) == 1
    assert len(gate.finish_runs) == 1
    assert mocks["events"] == ["begin_run", "call_managed", "finish_run"]
    assert gate.begin_runs[0] == {"task_uuid": TASK_UUID, "engine": "claude"}
    assert gate.finish_runs[0] == {"task_uuid": TASK_UUID}


def test_finish_run_called_when_call_managed_raises(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    mocks["call_managed"].side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        _execute(ROLE, "prompt")

    assert mocks["gate"].finish_runs == [{"task_uuid": TASK_UUID}]


def test_finish_run_called_on_timeout(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    mocks["call_managed"].side_effect = subprocess.TimeoutExpired(cmd="llm", timeout=600)

    result = _execute(ROLE, "prompt")

    assert result.returncode == 124
    assert mocks["gate"].finish_runs == [{"task_uuid": TASK_UUID}]


def test_rate_limit_retry_redeclares_alt_engine(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda text: True)
    mocks["call_managed"].side_effect = [
        _result("claude", returncode=1, body="rate limit exceeded"),
        _result("codex"),
    ]

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    calls = mocks["call_managed"].calls
    assert len(calls) == 2
    retry_engine = calls[1]["engine"]
    assert retry_engine != "claude"
    assert mocks["gate"].begin_runs == [
        {"task_uuid": TASK_UUID, "engine": "claude"},
        {"task_uuid": TASK_UUID, "engine": retry_engine},
    ]
    events = mocks["events"]
    begin_idx = [i for i, n in enumerate(events) if n == "begin_run"]
    call_idx = [i for i, n in enumerate(events) if n == "call_managed"]
    assert begin_idx[1] < call_idx[1]
    assert len(mocks["gate"].finish_runs) == 1


def test_rate_limit_retry_denied_raises_quota_paused(mocks, monkeypatch):
    _use_engine(monkeypatch, "claude")
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda text: True)
    mocks["call_managed"].side_effect = [
        _result("claude", returncode=1, body="rate limit exceeded"),
        _result("codex"),
    ]
    resume_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    mocks["gate"].begin_run_side_effect = [
        _allowed(),
        AdmissionDecision(
            allowed=False, status="DEFERRED", reason="drain", resume_at=resume_at
        ),
    ]

    with pytest.raises(RetrySignal) as exc_info:
        _execute(ROLE, "prompt")

    assert exc_info.value.reason == RetryReason.QUOTA_PAUSED
    assert exc_info.value.after == resume_at
    assert len(mocks["call_managed"].calls) == 1
    assert len(mocks["gate"].finish_runs) == 1


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
    assert len(mocks["call_managed"].calls) == 1
    assert mocks["gate"].begin_runs == []
    assert mocks["gate"].finish_runs == []


def test_begin_run_oserror_is_logged_and_ignored(mocks, monkeypatch, capsys):
    _use_engine(monkeypatch, "claude")
    mocks["gate"].begin_run_side_effect = OSError("disk full")

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    assert len(mocks["call_managed"].calls) == 1
    assert "quota declare failed" in capsys.readouterr().err


def test_finish_run_valueerror_is_logged_and_ignored(mocks, monkeypatch, capsys):
    _use_engine(monkeypatch, "claude")
    mocks["gate"].finish_run_side_effect = ValueError("bad state")

    result = _execute(ROLE, "prompt")

    assert result.returncode == 0
    assert "quota declare failed" in capsys.readouterr().err
