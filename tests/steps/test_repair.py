"""Tests for steps/repair.py (#3671).

AC-3: repair step runs engine once, no self-recursion, empty violations → andon(broken).
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.config import StepConfig
from issuesmith.steps.base import StepContext
from issuesmith.steps.repair import run


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
    return StepConfig(module="issuesmith.steps.repair", template=template)


# ---------------------------------------------------------------------------
# AC-3: empty violations → andon(broken)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# AC-3: no template → andon(broken)
# ---------------------------------------------------------------------------


def test_repair_no_template_returns_andon_broken() -> None:
    ctx = _ctx()
    result = run(ctx, StepConfig(module="issuesmith.steps.repair"))
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "template" in result.andon.summary


# ---------------------------------------------------------------------------
# AC-3: run_guarded called once on success
# ---------------------------------------------------------------------------


def test_repair_calls_run_guarded_once_on_success() -> None:
    ctx = _ctx()
    with patch("issuesmith.steps.repair.run_guarded", return_value=0) as mock_rg:
        with patch("issuesmith.steps.repair.get_config") as mock_cfg:
            mock_cfg.return_value.paths.template_dir = MagicMock()
            mock_cfg.return_value.paths.template_dir.__truediv__ = lambda s, o: "/tmp/repair.md"
            result = run(ctx, _step())

    assert mock_rg.call_count == 1
    assert result.status == "done"


def test_repair_run_guarded_failure_returns_nonzero_exit() -> None:
    ctx = _ctx()
    with patch("issuesmith.steps.repair.run_guarded", return_value=1):
        with patch("issuesmith.steps.repair.get_config") as mock_cfg:
            mock_cfg.return_value.paths.template_dir = MagicMock()
            mock_cfg.return_value.paths.template_dir.__truediv__ = lambda s, o: "/tmp/repair.md"
            result = run(ctx, _step())

    assert result.exit_code is not None
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# AC-3b: ISSUESMITH_REPAIR_ACTIVE → andon(broken) immediately
# ---------------------------------------------------------------------------


def test_repair_active_env_returns_andon_broken(monkeypatch) -> None:
    monkeypatch.setenv("ISSUESMITH_REPAIR_ACTIVE", "1")
    ctx = _ctx()
    result = run(ctx, _step())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "broken"
    assert "re-entered" in result.andon.summary


def test_repair_sets_active_env_during_run_guarded() -> None:
    """run_guarded is called with ISSUESMITH_REPAIR_ACTIVE set."""
    captured_env: list[str] = []

    def _capture_rg(*args, **kwargs):
        captured_env.append(os.environ.get("ISSUESMITH_REPAIR_ACTIVE", ""))
        return 0

    ctx = _ctx()
    with patch("issuesmith.steps.repair.run_guarded", side_effect=_capture_rg):
        with patch("issuesmith.steps.repair.get_config") as mock_cfg:
            mock_cfg.return_value.paths.template_dir = MagicMock()
            mock_cfg.return_value.paths.template_dir.__truediv__ = lambda s, o: "/tmp/repair.md"
            run(ctx, _step())

    assert captured_env == ["1"]


def test_repair_restores_env_after_run_guarded(monkeypatch) -> None:
    """ISSUESMITH_REPAIR_ACTIVE is cleaned up after run_guarded completes."""
    monkeypatch.delenv("ISSUESMITH_REPAIR_ACTIVE", raising=False)
    ctx = _ctx()
    with patch("issuesmith.steps.repair.run_guarded", return_value=0):
        with patch("issuesmith.steps.repair.get_config") as mock_cfg:
            mock_cfg.return_value.paths.template_dir = MagicMock()
            mock_cfg.return_value.paths.template_dir.__truediv__ = lambda s, o: "/tmp/repair.md"
            run(ctx, _step())

    assert os.environ.get("ISSUESMITH_REPAIR_ACTIVE") is None


# ---------------------------------------------------------------------------
# AC-3: RetrySignal propagates unchanged
# ---------------------------------------------------------------------------


def test_repair_propagates_retry_signal() -> None:
    from issuesmith.engine import RetryReason, RetrySignal

    ctx = _ctx()
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)
    with patch("issuesmith.steps.repair.run_guarded", side_effect=sig):
        with patch("issuesmith.steps.repair.get_config") as mock_cfg:
            mock_cfg.return_value.paths.template_dir = MagicMock()
            mock_cfg.return_value.paths.template_dir.__truediv__ = lambda s, o: "/tmp/repair.md"
            with pytest.raises(RetrySignal):
                run(ctx, _step())
