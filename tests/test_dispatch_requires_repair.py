"""dispatch requires: repair loop guards and round limits."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag.workflow.gates import Violation

from tests import test_dispatch_requires as _base
from tests.test_dispatch_requires import (
    RepairStepBoundary,
    _fail_gate,
    _make_ctx,
    _raised_andons,
    _RecordingForge,
    _v,
    stand_in_repair_step,
)

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")



class TestDispatchRepairOnePass:
    """AC-3: non-fixable violation → repair step runs once → pass → markers emitted."""

    def test_repair_once_then_pass_emits_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="tests.pytest_failure", auto_fixable=False)],
            [],
        ]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out

    def test_repair_step_failure_returns_nonzero(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = _fail_gate(rule_id="tests.pytest_failure", auto_fixable=False)

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                # repair step itself fails (returns 1)
                with stand_in_repair_step(exit_code=1):
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc == 1


class TestDispatchMaxRepairs:
    """AC-4: max_repairs exceeded → andon(decision) with fix_hints as options."""

    def test_max_repairs_exceeded_raises_andon_decision(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = _fail_gate(rule_id="tests.pytest_failure", auto_fixable=False)

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                        _repair_count=3,
                    )
        assert rc == 1
        raised = _raised_andons(forge)[0]
        assert raised.kind == "decision"

    def test_andon_decision_options_contain_fix_hints(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.return_value = [Violation(
            rule_id="tests.fail",
            severity="fail",
            message="tests broken",
            location=None,
            auto_fixable=False,
            fix_hint="add missing fixture",
        )]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                map_step_result(
                    StepResult(status="done", markers=["IMPL_DONE"]),
                    step_id="p2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                    _repair_count=3,
                )
        raised = _raised_andons(forge)[0]
        assert raised.kind == "decision"
        assert "split" in raised.options
        assert "reject" in raised.options


class TestDispatchRetryNotCounted:
    """AC-7: engine retry (RetrySignal) does not increment repair_count."""

    def test_retry_signal_from_repair_step_not_counted(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.engine import RetryReason, RetrySignal
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        # First call fails, second passes (repair would fix it)
        gate.check.side_effect = [
            [_v(rule_id="tests.fail", auto_fixable=False)],
            [],
        ]

        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                # repair step raises RetrySignal (quota)
                with stand_in_repair_step(side_effect=sig):
                    with pytest.raises(RetrySignal):
                        map_step_result(
                            StepResult(status="done", markers=["IMPL_DONE"]),
                            step_id="p2",
                            context=_make_ctx(),
                            step_cfg=cfg,
                        )


# ---------------------------------------------------------------------------
# AC-3b: repair step recursion guard and requires-skip
# ---------------------------------------------------------------------------


class TestDispatchRepairGuards:
    """AC-3b: ISSUESMITH_REPAIR_ACTIVE prevents re-entry; repair step skips requires."""

    def test_repair_step_skips_requires_evaluation(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(
            module="issuesmith.ops.repair_step",
            requires=("deps",),
            input_kind="issue",
        )
        with patch("issuesmith.ops.dispatch._build_requires_gates") as mock_build:
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                rc = map_step_result(
                    StepResult(status="done"),
                    step_id="repair",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        # _build_requires_gates must NOT be called for repair step
        mock_build.assert_not_called()
        assert rc == 0

    def test_dispatch_main_repair_active_produces_andon(self):
        import os

        from issuesmith.ops.dispatch import main

        with patch.dict(os.environ, {"ISSUESMITH_REPAIR_ACTIVE": "1"}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                rc = main(["repair", "issue_number=42", "workflow_name=issuesmith"])
        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert andon_arg is not None
        assert andon_arg.kind == "broken"
        assert "re-entered" in andon_arg.summary

    def test_bash_fallback_runs_with_repair_active_env(self, monkeypatch):
        import os

        from ghdag.workflow.gates import Violation

        from issuesmith.ops.dispatch import _run_repair_step

        monkeypatch.delenv("ISSUESMITH_REPAIR_ACTIVE", raising=False)
        seen: list[str | None] = []

        def _fake_bash(step_id, ctx):
            seen.append(os.environ.get("ISSUESMITH_REPAIR_ACTIVE"))
            return 0

        v = Violation(
            rule_id="deps.unmerged", severity="fail", message="x",
            location=None, auto_fixable=False, fix_hint="",
        )
        with patch("issuesmith.ops.dispatch._try_python_step", return_value=None):
            with patch("issuesmith.ops.dispatch._run_bash_step", side_effect=_fake_bash):
                rc = _run_repair_step([v], "p0", _make_ctx())
        assert rc is None
        assert seen == ["1"]
        assert os.environ.get("ISSUESMITH_REPAIR_ACTIVE") is None


class TestRepairGaveUp:
    """Repair REPAIR_FAILED → decision andon; requires loop stops without extra andon."""

    def test_repair_gave_up_returns_exit_one_without_re_eval(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import Andon, StepResult
        from issuesmith.ops.dispatch import run_requires_loop

        gave_up = StepResult(
            status="andon",
            andon=Andon(
                kind="decision",
                rule_id="repair.gave_up",
                summary="repair gave up",
                options=["split", "reject"],
            ),
        )

        cfg = StepConfig(
            module="",
            requires=("tests",),
            input_kind="worktree",
            requires_declared=True,
        )
        context = _make_ctx()
        gates = {"tests": _fail_gate("tests.pytest_failure")}
        def fake_try(step_id, ctx):
            from issuesmith.ops.dispatch import map_step_result

            return map_step_result(gave_up, step_id=step_id, context=ctx)

        with (
            patch("issuesmith.ops.dispatch._build_requires_gates", return_value=gates),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
            patch("issuesmith.ops.dispatch._try_python_step", side_effect=fake_try),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "p1", context)

        assert rc == 1
        raised_list = _raised_andons(forge)
        assert len(raised_list) == 1
        assert raised_list[0].rule_id == "repair.gave_up"


class TestAutoFixRoundLimit:
    """A gate that keeps failing after a successful fix() must not loop until the task timeout."""

    def test_auto_fixable_violation_recurring_is_waived_after_limit(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import _MAX_AUTO_FIX_ROUNDS, map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("base_freshness",))
        gate = MagicMock()
        # behind_base every time: the base branch moves between evaluations
        gate.check.side_effect = lambda body, labels: [_v(rule_id="base_freshness.behind_base", auto_fixable=True)]
        gate.fix.side_effect = lambda inp: inp

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"base_freshness": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p1",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        out = capsys.readouterr().out
        assert rc == 0
        assert gate.fix.call_count == _MAX_AUTO_FIX_ROUNDS
        assert "auto-fix round limit" in out and "base_freshness.behind_base" in out
        assert "PIPELINE_STATUS: IMPL_DONE" in out
        assert repair.launches == []

    def test_gate_fix_raising_keeps_violation_and_does_not_recurse(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("base_freshness",))
        gate = MagicMock()
        gate.check.side_effect = lambda body, labels: [_v(rule_id="base_freshness.behind_base", auto_fixable=True)]
        gate.fix.side_effect = RuntimeError("rebase conflict")

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"base_freshness": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(exit_code=1):
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p1",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc != 0
        assert gate.fix.call_count == 1
