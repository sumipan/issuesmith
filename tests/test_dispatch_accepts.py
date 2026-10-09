"""Tests for accepts evaluation in dispatch map_step_result (#4804 / #4886)."""
from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

from ghdag.workflow.gates import Violation

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import StepResult


def _v(rule_id: str = "test.fail", auto_fixable: bool = False) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity="fail",
        message="test violation",
        location=None,
        auto_fixable=auto_fixable,
        fix_hint="fix it" if auto_fixable else None,
    )


def _pass_gate() -> MagicMock:
    g = MagicMock()
    g.check.return_value = []
    return g


def _fail_gate(rule_id: str = "test.fail", auto_fixable: bool = False) -> MagicMock:
    g = MagicMock()
    g.check.return_value = [_v(rule_id, auto_fixable)]
    return g


def _make_ctx(issue: str = "42") -> dict:
    return {
        "issue_number": issue,
        "workflow_name": "issuesmith",
        "base_branch": "main",
    }


def _map_done(cfg: StepConfig, **kwargs):
    from issuesmith.ops.dispatch import map_step_result

    return map_step_result(
        StepResult(status="done", markers=["IMPL_DONE"]),
        step_id="p2",
        context=_make_ctx(),
        step_cfg=cfg,
        **kwargs,
    )


class TestDispatchAcceptsPass:
    def test_accepts_only_step_pass_emits_markers(self, capsys):
        cfg = StepConfig(module="issuesmith.steps.test", accepts=("lint",))
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"lint": _pass_gate()},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                rc = _map_done(cfg)
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out

    def test_requires_pass_then_accepts_pass_emits_markers(self, capsys):
        cfg = StepConfig(
            module="issuesmith.steps.test",
            requires=("scope",),
            accepts=("lint",),
        )
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            side_effect=[
                {"scope": _pass_gate()},
                {"lint": _pass_gate()},
            ],
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                rc = _map_done(cfg)
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out


class TestDispatchAcceptsRepair:
    def test_accepts_repair_once_then_pass_emits_markers(self, capsys):
        cfg = StepConfig(module="issuesmith.steps.test", accepts=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="tests.pytest_failure", auto_fixable=False)],
            [],
        ]
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"tests": gate},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                with patch("issuesmith.ops.dispatch._run_repair_step", return_value=None):
                    rc = _map_done(cfg)
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out


class TestDispatchAcceptsMaxRepairs:
    def test_accepts_max_repairs_exceeded_raises_andon_no_markers(self, capsys):
        from issuesmith.ops.dispatch import _MAX_REPAIRS, run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", accepts=("tests",))
        gate = _fail_gate(rule_id="tests.pytest_failure", auto_fixable=False)
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"tests": gate},
        ):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                mock_forge.return_value = MagicMock()
                with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                    with patch("issuesmith.ops.dispatch._run_repair_step", return_value=None):
                        rc = run_requires_loop(
                            cfg,
                            "p2",
                            _make_ctx(),
                            origin="accepts",
                            repair_count=_MAX_REPAIRS,
                        )
        assert rc == 1
        mock_andon.assert_called_once()
        assert "PIPELINE_STATUS" not in capsys.readouterr().out


class TestDispatchAcceptsOrdering:
    def test_requires_failure_skips_accepts(self):
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(
            module="issuesmith.steps.test",
            requires=("scope",),
            accepts=("lint",),
        )
        origins: list[str] = []

        def fake_loop(step_cfg, step_id, context, **kwargs):
            origins.append(kwargs.get("origin", "requires"))
            if kwargs.get("origin", "requires") == "requires":
                return 1
            return None

        with patch("issuesmith.ops.dispatch.run_requires_loop", side_effect=fake_loop):
            rc = map_step_result(
                StepResult(status="done", markers=["IMPL_DONE"]),
                step_id="p2",
                context=_make_ctx(),
                step_cfg=cfg,
            )
        assert rc == 1
        assert origins == ["requires"]

    def test_empty_accepts_does_not_run_accepts_loop(self):
        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))
        origins: list[str] = []

        def fake_loop(step_cfg, step_id, context, **kwargs):
            origins.append(kwargs.get("origin", "requires"))
            return None

        with patch("issuesmith.ops.dispatch.run_requires_loop", side_effect=fake_loop):
            rc = _map_done(cfg)
        assert rc == 0
        assert origins == ["requires"]


class TestDispatchAcceptsMetrics:
    def test_requires_check_records_origin(self, tmp_path, monkeypatch):
        from issuesmith.ops import dispatch as dispatch_mod
        from issuesmith.ops.dispatch import run_requires_loop

        metrics_path = tmp_path / "metrics.jsonl"
        cfg_obj = replace(get_config(), paths=replace(get_config().paths, metrics=metrics_path))
        monkeypatch.setattr(dispatch_mod, "get_config", lambda: cfg_obj)

        step_cfg = StepConfig(module="issuesmith.steps.test", accepts=("lint",))
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"lint": _pass_gate()},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                assert run_requires_loop(step_cfg, "p2", _make_ctx(), origin="accepts") is None

        line = json.loads(metrics_path.read_text().strip())
        assert line["event"] == "requires_check"
        assert line["origin"] == "accepts"

    def test_requires_repair_records_origin(self, tmp_path, monkeypatch):
        from issuesmith.ops import dispatch as dispatch_mod
        from issuesmith.ops.dispatch import run_requires_loop

        metrics_path = tmp_path / "metrics.jsonl"
        cfg_obj = replace(get_config(), paths=replace(get_config().paths, metrics=metrics_path))
        monkeypatch.setattr(dispatch_mod, "get_config", lambda: cfg_obj)

        gate = _fail_gate(rule_id="tests.pytest_failure", auto_fixable=False)
        step_cfg = StepConfig(module="issuesmith.steps.test", accepts=("tests",))
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"tests": gate},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                with patch("issuesmith.ops.dispatch._run_repair_step", return_value=1):
                    rc = run_requires_loop(step_cfg, "p2", _make_ctx(), origin="accepts")
        assert rc == 1
        line = json.loads(metrics_path.read_text().strip())
        assert line["event"] == "requires_repair"
        assert line["origin"] == "accepts"
        assert line["rule_ids"] == ["tests.pytest_failure"]

    def test_empty_accepts_does_not_add_extra_metrics_lines(self, tmp_path, monkeypatch):
        from issuesmith.ops import dispatch as dispatch_mod
        from issuesmith.ops.dispatch import run_requires_loop

        metrics_path = tmp_path / "metrics.jsonl"
        cfg_obj = replace(get_config(), paths=replace(get_config().paths, metrics=metrics_path))
        monkeypatch.setattr(dispatch_mod, "get_config", lambda: cfg_obj)

        step_cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"lint": _pass_gate()},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                assert run_requires_loop(step_cfg, "p2", _make_ctx(), origin="requires") is None

        lines = [ln for ln in metrics_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["origin"] == "requires"
