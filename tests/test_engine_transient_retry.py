"""Guarded orders retry empty / unclassified LLM output via quota pause (#7397)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from ghdag.llm import LLMResult
from ghdag.metrics import FailureClass
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.contract import RetryReason, RetrySignal
from issuesmith.engine import (
    _ENGINE_TRANSIENT_PAUSE_SEC,
    _GUARDED_TRANSIENT_MAX,
    RoleSelection,
    _EngineProcess,
    _execute,
    _guarded_transient_count,
    _increment_guarded_transient_count,
    run_guarded_output,
)

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _T0 if tz is None else _T0.astimezone(tz)


def _empty_failure(engine: str = "codex") -> _EngineProcess:
    return _EngineProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="",
        failure_class=None,
        engine_used=engine,
    )


def _empty_success(engine: str = "codex") -> _EngineProcess:
    return _EngineProcess(
        args=[],
        returncode=0,
        stdout="",
        stderr="",
        failure_class=None,
        engine_used=engine,
    )


def test_run_guarded_output_empty_unclassified_raises_retry_signal(
    monkeypatch, tmp_path
) -> None:
    """Refs #7397 — non-zero exit with no stdout, stderr, or failure class waits."""
    monkeypatch.setattr(
        "issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE",
        tmp_path / "guarded-transient-retry.json",
    )
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _empty_failure(),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = MagicMock()
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate", lambda state_path: quota_gate
    )
    monkeypatch.setenv("GHDAG_TASK_UUID", "empty-unclassified-task")

    with pytest.raises(RetrySignal) as exc_info:
        run_guarded_output(
            "review",
            "/tmp/t.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )

    assert exc_info.value.reason is RetryReason.ENGINE_TRANSIENT
    quota_gate.report.assert_called_once()


def test_run_guarded_output_empty_success_raises_retry_signal(
    monkeypatch, tmp_path
) -> None:
    """Refs #7397 — exit 0 with empty stdout is treated as a transient engine fault."""
    monkeypatch.setattr(
        "issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE",
        tmp_path / "guarded-transient-retry.json",
    )
    monkeypatch.setenv("GHDAG_TASK_UUID", "empty-success-task")
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _empty_success(),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    monkeypatch.setattr(
        "issuesmith.engine.QuotaGate", lambda state_path: MagicMock()
    )

    with pytest.raises(RetrySignal):
        run_guarded_output(
            "review",
            "/tmp/t.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )


def test_run_guarded_output_transient_with_stderr_stays_failure() -> None:
    """Refs #7397 — stderr classifies the failure; no quota retry."""
    proc = _EngineProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="boom",
        failure_class=None,
        engine_used="codex",
    )
    with (
        patch("issuesmith.engine._execute", return_value=proc),
        patch("issuesmith.engine._render_template", return_value="order"),
    ):
        rc, stdout = run_guarded_output(
            "review",
            "/tmp/t.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )

    assert rc == 1
    assert "ENGINE_STDERR_TAIL: boom" in stdout


def test_counter_increments_per_task_and_template(
    monkeypatch, tmp_path
) -> None:
    """Refs #7397 — retries are counted under GHDAG_TASK_UUID and template name."""
    state = tmp_path / "guarded-transient-retry.json"
    monkeypatch.setattr("issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE", state)
    task_uuid = "task-uuid-1"
    template = "cp2.md"

    assert _increment_guarded_transient_count(task_uuid, template) == 1
    assert _guarded_transient_count(task_uuid, template) == 1
    assert _increment_guarded_transient_count(task_uuid, template) == 2


def test_transient_exhausted_returns_guarded_failure(monkeypatch, tmp_path) -> None:
    """Refs #7397 — fourth empty failure becomes a reviewable engine_error path."""
    state = tmp_path / "guarded-transient-retry.json"
    monkeypatch.setattr("issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE", state)
    monkeypatch.setenv("GHDAG_TASK_UUID", "exhaust-task")
    key_task = "exhaust-task"
    for _ in range(_GUARDED_TRANSIENT_MAX):
        _increment_guarded_transient_count(key_task, "cp2.md")

    with (
        patch("issuesmith.engine._execute", return_value=_empty_failure()),
        patch("issuesmith.engine._render_template", return_value="order"),
    ):
        rc, stdout = run_guarded_output(
            "review",
            "/tmp/cp2.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )

    assert rc == 1
    assert "ENGINE_FAILURE: none" in stdout


def test_run_guarded_order_without_task_uuid_does_not_retry(
    monkeypatch, tmp_path
) -> None:
    """Refs #7397 — runs without GHDAG_TASK_UUID fall through to guarded failure."""
    state = tmp_path / "engine-transient-retries.json"
    monkeypatch.setattr("issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE", state)
    monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
    with (
        patch("issuesmith.engine._execute", return_value=_empty_success()),
        patch("issuesmith.engine._render_template", return_value="order"),
    ):
        rc, stdout = run_guarded_output(
            "review",
            "/tmp/cp2.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )

    assert rc == 1
    assert not state.exists()


def test_execute_engine_error_reason_unchanged(monkeypatch) -> None:
    """Refs #7397 — _execute ENGINE_ERROR still reports engine_transient: ENGINE_ERROR."""
    quota_gate = MagicMock()
    quota_gate.snapshot.return_value = QuotaSnapshot(
        engines={
            "claude": EngineQuotaState(
                status="available", observed_at=_T0, resume_at=None, reason=None
            ),
            "codex": EngineQuotaState(
                status="available", observed_at=_T0, resume_at=None, reason=None
            ),
            "cursor": EngineQuotaState(
                status="available", observed_at=_T0, resume_at=None, reason=None
            ),
        },
        draining_engines={},
        running_tasks={},
        limits={},
    )
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: quota_gate)
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    monkeypatch.setattr(
        "issuesmith.engine.HEAVY_ROUTES",
        {"review": (("codex", "gpt"),)},
    )
    monkeypatch.setattr("issuesmith.engine.LIGHT_ROUTES", {"review": None})
    monkeypatch.setattr("issuesmith.engine.ROLE_ENGINES", {"review": ("codex",)})
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="codex", model="gpt"),
    )
    monkeypatch.setattr(
        "issuesmith.engine.call",
        lambda *_a, **_k: LLMResult(
            body="",
            stdout="",
            stderr="",
            returncode=1,
            failure_class=FailureClass.ENGINE_ERROR,
            engine_used="codex",
            model_used="m",
        ),
    )
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    with pytest.raises(RetrySignal):
        _execute("review", "prompt")

    kwargs = quota_gate.report.call_args.kwargs
    assert kwargs["reason"] == "engine_transient: ENGINE_ERROR"


def test_raise_engine_transient_uses_configured_pause(monkeypatch) -> None:
    """Refs #7397 — guarded and execute paths share the same quota pause duration."""
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = MagicMock()
    expected_after = _T0 + timedelta(seconds=_ENGINE_TRANSIENT_PAUSE_SEC)

    with pytest.raises(RetrySignal) as exc_info:
        from issuesmith.engine import _raise_engine_transient

        _raise_engine_transient(
            engine="codex",
            role="review",
            quota_gate=quota_gate,
            reason="engine_transient: empty_output",
        )

    assert exc_info.value.after == expected_after
