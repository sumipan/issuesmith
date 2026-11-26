"""Tests for failure diagnostics in engine._execute (#3611, #4239)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.metrics import FailureClass
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.contract import RetrySignal
from issuesmith.engine import (
    _ENGINE_TRANSIENT_PAUSE_SEC,
    RoleSelection,
    _execute,
    _failure_class_from_value,
    _truncate_failure_stderr,
)

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _T0 if tz is None else _T0.astimezone(tz)


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
    return EngineQuotaState(
        status="available",
        observed_at=_T0,
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
    return {"recorded": recorded, "quota_gate": quota_gate}


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


def test_execute_python_bin_failure_sets_engine_environment_error(
    execute_mocks, monkeypatch, tmp_path: Path,
):
    """Ref: sumipan/nexus#8074 AC7 — venv resolution failure classifies before LLM call."""
    from issuesmith.worktree_python import WorktreePythonError

    def _raise_bin(_cwd: object) -> None:
        raise WorktreePythonError("venv missing")

    monkeypatch.setattr("issuesmith.engine.python_bin_for", _raise_bin)
    proc = _execute("implementation", "prompt", cwd=tmp_path)

    assert proc.returncode == 1
    assert proc.failure_class == FailureClass.ENGINE_ENVIRONMENT_ERROR.value
    assert "venv missing" in proc.stderr


def test_execute_engine_error_pauses_engine_via_quota_gate(execute_mocks, monkeypatch):
    """Refs sumipan/nexus#6604 — ENGINE_ERROR reports a single quota pause."""
    call_count = {"n": 0}

    def fake_call(*_args, **_kwargs):
        call_count["n"] += 1
        return LLMResult(
            body="",
            stdout="",
            stderr="",
            returncode=1,
            failure_class=FailureClass.ENGINE_ERROR,
            engine_used="codex",
            model_used="m",
        )

    monkeypatch.setattr(
        "issuesmith.engine.HEAVY_ROUTES",
        {"review": (("codex", "gpt"),)},
    )
    monkeypatch.setattr(
        "issuesmith.engine.LIGHT_ROUTES",
        {"review": None},
    )
    monkeypatch.setattr("issuesmith.engine.ROLE_ENGINES", {"review": ("codex",)})
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="codex", model="gpt"),
    )
    monkeypatch.setattr("issuesmith.engine.call", fake_call)
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = execute_mocks["quota_gate"]
    with pytest.raises(RetrySignal) as exc_info:
        _execute("review", "prompt")

    quota_gate.report.assert_called_once()
    kwargs = quota_gate.report.call_args.kwargs
    assert kwargs["engine"] == "codex"
    assert kwargs["status"] == "paused"
    assert kwargs["reason"] == "engine_transient: ENGINE_ERROR"
    assert call_count["n"] == 1
    expected_after = _T0 + timedelta(seconds=_ENGINE_TRANSIENT_PAUSE_SEC)
    assert exc_info.value.after == expected_after


