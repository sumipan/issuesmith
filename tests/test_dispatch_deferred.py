"""dispatch: RetrySignal -> QuotaGate.defer + PIPELINE_STATUS: DEFERRED (sumipan/nexus#3515)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from ghdag.quota import QuotaGate

from issuesmith.engine import ROLE_ENGINES, RetryReason, RetrySignal
from issuesmith.ops import dispatch as dispatch_mod


@pytest.fixture
def gate(tmp_path: Path) -> QuotaGate:
    return QuotaGate(state_path=tmp_path / "quota-gate.json")


def _deferred(gate: QuotaGate) -> dict:
    return json.loads(gate._state_path.read_text(encoding="utf-8"))["deferred_tasks"]


def test_retry_signal_registers_deferred_task_and_prints_status(gate, capsys):
    after = datetime.now(timezone.utc) + timedelta(minutes=30)
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=after, role="design")

    dispatch_mod._handle_retry_signal(sig, "p1", None, quota_gate=gate, task_uuid="task-123")

    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: DEFERRED" in out
    deferred = _deferred(gate)
    assert set(deferred) == {"task-123"}
    entry = deferred["task-123"]
    assert entry["engine"] in ROLE_ENGINES["design"]
    assert set(entry["role_engines"]) == set(ROLE_ENGINES["design"])
    assert entry["reason"] == "p1: QUOTA_PAUSED"


def test_task_uuid_read_from_ghdag_env(gate, monkeypatch, capsys):
    monkeypatch.setenv("GHDAG_TASK_UUID", "env-uuid")
    sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None, role="implementation")

    dispatch_mod._handle_retry_signal(sig, "impl", None, quota_gate=gate)

    assert "PIPELINE_STATUS: DEFERRED" in capsys.readouterr().out
    assert set(_deferred(gate)) == {"env-uuid"}


def test_missing_task_uuid_still_reports_deferred_without_registration(gate, monkeypatch, capsys):
    monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")

    dispatch_mod._handle_retry_signal(sig, "p1", None, quota_gate=gate)

    captured = capsys.readouterr()
    assert "PIPELINE_STATUS: DEFERRED" in captured.out
    assert "GHDAG_TASK_UUID" in captured.err
    assert not gate._state_path.exists() or _deferred(gate) == {}


def test_deferred_task_is_released_by_release_ready(gate, capsys):
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
    dispatch_mod._handle_retry_signal(sig, "p1", None, quota_gate=gate, task_uuid="task-9")
    capsys.readouterr()

    released = gate.release_ready(now=datetime.now(timezone.utc) + timedelta(hours=1))

    assert released == ["task-9"]


def test_retry_signal_keeps_role():
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
    assert sig.role == "design"
