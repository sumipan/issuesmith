"""Tests for ops/repair_step.py (#3671 / #4276).

AC-3: repair step runs engine once, no self-recursion, empty violations → andon(broken).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.config import StepConfig
from issuesmith.contract import StepContext
from issuesmith.ops.repair_step import run


def _ctx(**kwargs) -> StepContext:
    defaults = dict(
        issue_number="42",
        base_branch="main",
        handler_name="test",
        is_cross_repo="false",
        target_clone_path="",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        repair_violations="- deps.unmerged: issue #99 not merged",
        repair_step_origin="p0",
        worktree_path="/tmp/wt",
        branch="feat/test-123",
    )
    defaults.update(kwargs)
    return StepContext(**defaults)


def _step(template: str = "repair.md") -> StepConfig:
    return StepConfig(module="issuesmith.ops.repair_step", template=template)


def _configure_mock_get_config(mock_cfg, steps=None) -> None:
    mock_cfg.return_value.paths.template_dir = MagicMock()
    mock_cfg.return_value.paths.template_dir.__truediv__ = lambda s, o: "/tmp/repair.md"
    mock_cfg.return_value.steps = steps if steps is not None else {}


def test_repair_empty_violations_returns_andon_broken() -> None:
    ctx = _ctx(repair_violations="")
    result = run(ctx, _step())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "without violations" in result.andon.summary


def test_repair_whitespace_only_violations_returns_andon_broken() -> None:
    ctx = _ctx(repair_violations="   \n  ")
    result = run(ctx, _step())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"


def test_repair_no_template_returns_andon_broken() -> None:
    ctx = _ctx()
    result = run(ctx, StepConfig(module="issuesmith.ops.repair_step"))
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "template" in result.andon.summary


def test_repair_calls_run_guarded_once_on_success() -> None:
    ctx = _ctx()
    with patch(
        "issuesmith.ops.repair_step.run_guarded_output", return_value=(0, "")
    ) as mock_rg:
        with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
            _configure_mock_get_config(mock_cfg)
            result = run(ctx, _step())

    assert mock_rg.call_count == 1
    assert result.status == "done"


def test_repair_run_guarded_failure_returns_nonzero_exit() -> None:
    ctx = _ctx()
    with patch(
        "issuesmith.ops.repair_step.run_guarded_output", return_value=(1, "timeout")
    ):
        with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
            _configure_mock_get_config(mock_cfg)
            result = run(ctx, _step())

    assert result.exit_code is not None
    assert result.exit_code != 0
    assert result.status == "done"


def test_repair_active_env_returns_andon_broken(monkeypatch) -> None:
    monkeypatch.setenv("ISSUESMITH_REPAIR_ACTIVE", "1")
    ctx = _ctx()
    result = run(ctx, _step())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "re-entered" in result.andon.summary


def test_repair_sets_active_env_during_run_guarded() -> None:
    captured_env: list[str] = []

    def _capture_rg(*args, **kwargs):
        captured_env.append(os.environ.get("ISSUESMITH_REPAIR_ACTIVE", ""))
        return (0, "")

    ctx = _ctx()
    with patch(
        "issuesmith.ops.repair_step.run_guarded_output",
        side_effect=_capture_rg,
    ):
        with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
            _configure_mock_get_config(mock_cfg)
            run(ctx, _step())

    assert captured_env == ["1"]


def test_repair_restores_env_after_run_guarded(monkeypatch) -> None:
    monkeypatch.delenv("ISSUESMITH_REPAIR_ACTIVE", raising=False)
    ctx = _ctx()
    with patch("issuesmith.ops.repair_step.run_guarded_output", return_value=(0, "")):
        with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
            _configure_mock_get_config(mock_cfg)
            run(ctx, _step())

    assert os.environ.get("ISSUESMITH_REPAIR_ACTIVE") is None


def test_repair_push_true_runs_git_push() -> None:
    from issuesmith.config import RepairConfig, StepConfig

    origin = StepConfig(
        module="issuesmith.ops.repair_step",
        repair=RepairConfig(push=True),
    )
    ctx = _ctx(repair_step_origin="p0")
    with patch("issuesmith.ops.repair_step.run_guarded_output", return_value=(0, "")):
        with patch("issuesmith.ops.repair_step.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
                _configure_mock_get_config(mock_cfg, steps={"p0": origin})
                result = run(ctx, _step())

    assert result.status == "done"
    push_calls = [
        c for c in mock_run.call_args_list if c.args and c.args[0][:2] == ["git", "push"]
    ]
    assert len(push_calls) == 1
    assert push_calls[0].args[0][:3] == ["git", "push", "origin"]


def test_repair_push_failure_returns_andon_broken() -> None:
    from issuesmith.config import RepairConfig, StepConfig

    origin = StepConfig(
        module="issuesmith.ops.repair_step",
        repair=RepairConfig(push=True),
    )
    ctx = _ctx(repair_step_origin="p0")
    with patch("issuesmith.ops.repair_step.run_guarded_output", return_value=(0, "")):
        with patch("issuesmith.ops.repair_step.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="denied")
            with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
                _configure_mock_get_config(mock_cfg, steps={"p0": origin})
                result = run(ctx, _step())

    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "repair push failed" in result.andon.summary


def test_repair_push_false_skips_git_push() -> None:
    from issuesmith.config import StepConfig

    origin = StepConfig(module="issuesmith.ops.repair_step")
    ctx = _ctx(repair_step_origin="p0")
    with patch("issuesmith.ops.repair_step.run_guarded_output", return_value=(0, "")):
        with patch("issuesmith.ops.repair_step.subprocess.run") as mock_run:
            with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
                _configure_mock_get_config(mock_cfg, steps={"p0": origin})
                result = run(ctx, _step())

    assert result.status == "done"
    push_calls = [
        c for c in mock_run.call_args_list if c.args and c.args[0][:2] == ["git", "push"]
    ]
    assert push_calls == []


def test_repair_propagates_retry_signal() -> None:
    from issuesmith.engine import RetryReason, RetrySignal

    ctx = _ctx()
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
    with patch("issuesmith.ops.repair_step.run_guarded_output", side_effect=sig):
        with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
            _configure_mock_get_config(mock_cfg)
            with pytest.raises(RetrySignal):
                run(ctx, _step())


def test_repair_failed_returns_decision_andon_with_widen_options(tmp_path) -> None:
    pub = tmp_path / "tools" / "newspaper" / "publish.py"
    test_pub = tmp_path / "tests" / "tools" / "newspaper" / "test_publish.py"
    pub.parent.mkdir(parents=True)
    test_pub.parent.mkdir(parents=True)
    pub.write_text("# x\n", encoding="utf-8")
    test_pub.write_text("# t\n", encoding="utf-8")

    stdout = (
        "reason: need changes in tests/tools/newspaper/test_publish.py "
        "and tools/newspaper/publish.py\n"
        "PIPELINE_STATUS: REPAIR_FAILED\n"
    )
    ctx = _ctx(
        worktree_path=str(tmp_path),
        repair_step_origin="p1",
        allow_paths="- src/**\n",
        derived_allow_paths="",
        repair_violation_summary="- pr_scope.out_of_allow: outside allow_paths",
        repair_widen_candidates="",
    )
    with patch(
        "issuesmith.ops.repair_step.run_guarded_output", return_value=(1, stdout)
    ):
        with patch("issuesmith.ops.repair_step.get_config") as mock_cfg:
            _configure_mock_get_config(mock_cfg)
            result = run(ctx, _step())

    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "decision"
    assert result.andon.rule_id == "repair.gave_up"
    assert result.andon.options == [
        "widen:tests/tools/newspaper/test_publish.py,tools/newspaper/publish.py",
        "split",
        "reject",
    ]
    assert "repair gave up in step p1" in result.andon.summary
    assert len(result.andon.summary) <= 600


def test_repair_widen_from_structured_candidates_without_stdout_paths(tmp_path) -> None:
    extra = tmp_path / "tools" / "extra.py"
    extra.parent.mkdir(parents=True)
    extra.write_text("x", encoding="utf-8")
    ctx = _ctx(
        worktree_path=str(tmp_path),
        allow_paths="- src/**",
        repair_widen_candidates="tools/extra.py",
    )
    from issuesmith.ops.repair_step import _decision_options

    options = _decision_options(ctx, "PIPELINE_STATUS: REPAIR_FAILED\n")
    assert options[0] == "widen:tools/extra.py"


def test_repair_widen_excludes_allowed_and_invalid_paths(tmp_path) -> None:
    allowed = tmp_path / "src" / "ok.py"
    allowed.parent.mkdir(parents=True)
    allowed.write_text("x", encoding="utf-8")
    ctx = _ctx(
        worktree_path=str(tmp_path),
        allow_paths="- src/**",
        derived_allow_paths="tests/**",
        repair_widen_candidates="src/ok.py\n/etc/passwd\nfoo/../bar.py",
    )
    from issuesmith.ops.repair_step import _decision_options

    options = _decision_options(ctx, "")
    assert options == ["split", "reject"]


def test_repair_no_widen_candidates_only_split_reject() -> None:
    ctx = _ctx(repair_widen_candidates="")
    from issuesmith.ops.repair_step import _decision_options

    assert _decision_options(ctx, "PIPELINE_STATUS: REPAIR_FAILED\n") == [
        "split",
        "reject",
    ]

