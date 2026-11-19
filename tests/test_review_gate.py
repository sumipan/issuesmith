"""Tests for gates.review.ReviewGate (#4986)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.config import ReviewConfig
from issuesmith.contract import RetryReason, RetrySignal
from issuesmith.engine import (
    _GUARDED_TRANSIENT_MAX,
    _EngineProcess,
    _increment_guarded_transient_count,
)
from issuesmith.gates import GateBuildContext, GateBuildError
from issuesmith.gates.review import ReviewGate, _violations_from_stdout, build_review_gate
from issuesmith.repair import evaluate_requires


def _review_cfg() -> ReviewConfig:
    return ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
    )


def test_check_pass_returns_empty(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    with patch(
        "issuesmith.engine.run_guarded_output",
        return_value=(0, "ok\nPIPELINE_STATUS: CP2_PASS\n"),
    ):
        assert gate.check("", []) == []


def test_check_fail_parses_bullets(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    stdout = "x\nProblems:\n- a\n- b\nPIPELINE_STATUS: CP2_FAIL\n"
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 2
    assert vs[0].rule_id == "review.x"
    assert vs[0].message == "a"
    assert vs[0].auto_fixable is False
    assert vs[1].message == "b"


def test_violations_without_heading_use_snippet() -> None:
    vs = _violations_from_stdout("short fail", _review_cfg(), "x")
    assert len(vs) == 1
    assert vs[0].message == "short fail"


def test_check_propagates_retry_signal(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
    with patch("issuesmith.engine.run_guarded_output", side_effect=sig):
        with pytest.raises(RetrySignal):
            gate.check("", [])


def test_check_propagates_engine_transient_retry_signal(tmp_path: Path) -> None:
    """Refs #7397 — review gate forwards guarded-order ENGINE_TRANSIENT retries."""
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    sig = RetrySignal(reason=RetryReason.ENGINE_TRANSIENT)
    with patch("issuesmith.engine.run_guarded_output", side_effect=sig):
        with pytest.raises(RetrySignal) as exc_info:
            gate.check("", [])
    assert exc_info.value.reason is RetryReason.ENGINE_TRANSIENT


_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _T0 if tz is None else _T0.astimezone(tz)


def _patch_review_template_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "cp2.md").write_text("order", encoding="utf-8")
    cfg = MagicMock()
    cfg.paths.template_dir = tmp_path
    monkeypatch.setattr("issuesmith.gates.review.get_config", lambda: cfg)


def test_check_empty_output_raises_engine_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC1 — exit 0 with empty stdout schedules ENGINE_TRANSIENT via review."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE",
        tmp_path / "engine-transient-retries.json",
    )
    monkeypatch.setenv("GHDAG_TASK_UUID", "review-empty-out")
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: quota_gate)
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    with pytest.raises(RetrySignal) as exc_info:
        gate.check("", [])
    assert exc_info.value.reason is RetryReason.ENGINE_TRANSIENT
    assert quota_gate.report.call_args.kwargs["reason"] == "engine_transient: empty_output"


def test_check_unclassified_failure_raises_engine_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC2 — unclassified non-zero exit schedules ENGINE_TRANSIENT."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE",
        tmp_path / "engine-transient-retries.json",
    )
    monkeypatch.setenv("GHDAG_TASK_UUID", "review-unclassified")
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: quota_gate)
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    with pytest.raises(RetrySignal):
        gate.check("", [])
    assert (
        quota_gate.report.call_args.kwargs["reason"]
        == "engine_transient: unclassified_failure"
    )


def test_check_transient_over_limit_returns_engine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC3 — after retry cap, review maps empty output to engine_error."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    state_path = tmp_path / "engine-transient-retries.json"
    monkeypatch.setattr("issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE", state_path)
    task_uuid = "review-over-limit"
    monkeypatch.setenv("GHDAG_TASK_UUID", task_uuid)
    for _ in range(_GUARDED_TRANSIENT_MAX):
        _increment_guarded_transient_count(task_uuid, "cp2.md")
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert vs[0].message == "review failed without output"


def test_check_failure_marker_still_returns_review_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC4 — structured CP2_FAIL output stays review.cp2, not engine_error."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    stdout = "Problems:\n- bad thing\nPIPELINE_STATUS: CP2_FAIL\n"
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=1,
            stdout=stdout,
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2"
    assert vs[0].message == "bad thing"


def test_check_issue_body_fetch_failure_still_returns_engine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC5 — issue body fetch failure is engine_error, not transient retry."""
    template = tmp_path / "cp2.md"
    template.write_text("$issue_body_file\n", encoding="utf-8")
    cfg = MagicMock()
    cfg.paths.template_dir = tmp_path
    monkeypatch.setattr("issuesmith.gates.review.get_config", lambda: cfg)
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.fetch_issue_inputs",
        lambda parsed: ("", []),
    )
    execute = MagicMock()
    monkeypatch.setattr("issuesmith.engine._execute", execute)
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {"issue_number": "99"})
    vs = gate.check("", [])
    execute.assert_not_called()
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"


def test_evaluate_requires_propagates_retry_signal() -> None:
    gate = MagicMock()
    gate.check.side_effect = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
    with pytest.raises(RetrySignal):
        evaluate_requires({"review": gate}, "", [])


def test_evaluate_requires_gate_error_on_value_error() -> None:
    gate = MagicMock()
    gate.check.side_effect = ValueError("boom")
    result = evaluate_requires({"review": gate}, "", [])
    assert result.gate_error is not None


def test_build_review_gate_missing_step_id(tmp_path: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=tmp_path,
        allow_paths=[],
        base_branch="main",
    )
    with pytest.raises(GateBuildError):
        build_review_gate(ctx)


def test_build_review_gate_missing_review_config(tmp_path: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=tmp_path,
        allow_paths=[],
        base_branch="main",
        step_id="missing",
    )
    with pytest.raises(GateBuildError):
        build_review_gate(ctx)


_FILL_LINE = (
    "[issuesmith-review] step=cp2 optional variables missing from order, "
    "filled empty: execution_constraints"
)


def _optional_cfg(*names: str) -> ReviewConfig:
    return ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
        optional_variables=names,
    )


def _run_check(gate: ReviewGate) -> list[str]:
    with patch(
        "issuesmith.engine.run_guarded_output",
        return_value=(0, "PIPELINE_STATUS: CP2_PASS\n"),
    ) as mock_run:
        assert gate.check("", []) == []
    return mock_run.call_args.args[2]


def test_check_does_not_override_passed_optional_variable(
    tmp_path: Path, capsys
) -> None:
    gate = ReviewGate(
        _optional_cfg("execution_constraints"),
        "cp2",
        tmp_path,
        {"issue_number": "1", "execution_constraints": "abc"},
    )
    variables = _run_check(gate)
    assert "execution_constraints=abc" in variables
    assert "execution_constraints=" not in variables
    assert "optional variables missing" not in capsys.readouterr().err


def test_check_does_not_fill_unlisted_variable(tmp_path: Path) -> None:
    gate = ReviewGate(
        _optional_cfg("execution_constraints"), "cp2", tmp_path, {"issue_number": "1"}
    )
    variables = _run_check(gate)
    assert not any(v.startswith("other_var=") for v in variables)


def test_check_without_optional_variables_unchanged(tmp_path: Path, capsys) -> None:
    passed = {"issue_number": "1", "worktree": "w"}
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, passed)
    variables = _run_check(gate)
    assert variables == [f"{k}={v}" for k, v in passed.items()]
    assert "optional variables missing" not in capsys.readouterr().err


def test_check_engine_error_for_unstructured_stdout(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = '{"type":"thread.started","thread_id":"abc"}\n'
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert vs[0].auto_fixable is False
    assert "review engine exited 1:" in vs[0].message
    assert stdout.strip() in vs[0].message


def test_check_engine_error_empty_stdout(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, "")):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert vs[0].message == "review failed without output"


def test_check_failure_status_line_not_engine_error(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = "PIPELINE_STATUS: CP2_FAIL\n"
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2"
