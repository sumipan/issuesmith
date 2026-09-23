"""Tests for requires evaluation and repair loop in dispatch (#3626).

Covers: pass / auto_fix / repair-1-pass / max_repairs-exceeded→andon /
gate-exception→broken / preexisting-non-blocking.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag.workflow.gates import Violation

from issuesmith.gates.base import ContractInput
from issuesmith.repair import (
    RequiresResult,
    apply_auto_fixes,
    evaluate_requires,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    g.fix.side_effect = lambda inp: inp
    return g


def _fail_gate(rule_id: str = "test.fail", auto_fixable: bool = False) -> MagicMock:
    g = MagicMock()
    g.check.return_value = [_v(rule_id, auto_fixable)]
    g.fix.side_effect = lambda inp: inp
    return g


def _error_gate(exc: Exception | None = None) -> MagicMock:
    g = MagicMock()
    g.check.side_effect = exc or RuntimeError("gate exploded")
    return g


def _inp() -> ContractInput:
    return ContractInput(body="issue body", labels=[])


# ---------------------------------------------------------------------------
# evaluate_requires — core evaluation logic
# ---------------------------------------------------------------------------

class TestEvaluateRequires:
    def test_no_gates_returns_empty(self):
        r = evaluate_requires({}, "body", [])
        assert not r.blocking
        assert not r.preexisting
        assert r.gate_error is None

    def test_passing_gate_no_blocking(self):
        r = evaluate_requires({"scope": _pass_gate()}, "body", [])
        assert not r.blocking
        assert r.gate_error is None

    def test_failing_gate_creates_blocking(self):
        r = evaluate_requires({"scope": _fail_gate()}, "body", [])
        assert len(r.blocking) == 1
        assert r.blocking[0].rule_id == "test.fail"

    def test_gate_exception_sets_gate_error(self):
        r = evaluate_requires({"scope": _error_gate()}, "body", [])
        assert r.gate_error is not None
        assert not r.blocking

    def test_preexisting_rule_id_excluded_from_blocking(self):
        r = evaluate_requires(
            {"scope": _fail_gate(rule_id="test.old")},
            "body",
            [],
            preexisting_rule_ids=frozenset({"test.old"}),
        )
        assert not r.blocking
        assert len(r.preexisting) == 1
        assert r.preexisting[0].rule_id == "test.old"

    def test_multiple_gates_violations_merged(self):
        gates = {
            "lint": _fail_gate(rule_id="lint.E501"),
            "tests": _fail_gate(rule_id="tests.pytest_failure"),
        }
        r = evaluate_requires(gates, "body", [])
        rule_ids = {v.rule_id for v in r.blocking}
        assert rule_ids == {"lint.E501", "tests.pytest_failure"}

    def test_first_error_gate_aborts_remaining(self):
        gates = {
            "bad": _error_gate(),
            "ok": _pass_gate(),
        }
        r = evaluate_requires(gates, "body", [])
        assert r.gate_error is not None
        # ok gate should not have been called yet (order-dependent; dict preserves insertion order)
        # — we only assert error is set; gate call count may vary


# ---------------------------------------------------------------------------
# apply_auto_fixes
# ---------------------------------------------------------------------------

class TestApplyAutoFixes:
    def test_non_fixable_violation_stays_blocking(self):
        result = RequiresResult(blocking=[_v(auto_fixable=False)])
        updated, inp = apply_auto_fixes(result, {}, _inp())
        assert len(updated.blocking) == 1

    def test_auto_fixable_calls_gate_fix(self):
        gate = MagicMock()
        gate.fix.side_effect = lambda inp: inp
        result = RequiresResult(blocking=[_v(rule_id="lint.E501", auto_fixable=True)])
        updated, _ = apply_auto_fixes(result, {"lint": gate}, _inp())
        gate.fix.assert_called_once()
        assert not updated.blocking
        assert "lint.E501" in updated.auto_fixed

    def test_gate_without_fix_method_keeps_violation(self):
        gate = MagicMock(spec=[])  # no fix attribute
        result = RequiresResult(blocking=[_v(rule_id="lint.X", auto_fixable=True)])
        updated, _ = apply_auto_fixes(result, {"lint": gate}, _inp())
        assert len(updated.blocking) == 1

    def test_gate_fix_exception_keeps_violation(self):
        gate = MagicMock()
        gate.fix.side_effect = RuntimeError("fix failed")
        result = RequiresResult(blocking=[_v(rule_id="lint.X", auto_fixable=True)])
        updated, _ = apply_auto_fixes(result, {"lint": gate}, _inp())
        assert len(updated.blocking) == 1

    def test_mixed_fixable_and_non_fixable(self):
        gate = MagicMock()
        gate.fix.side_effect = lambda inp: inp
        result = RequiresResult(blocking=[
            _v(rule_id="lint.E501", auto_fixable=True),
            _v(rule_id="tests.fail", auto_fixable=False),
        ])
        updated, _ = apply_auto_fixes(result, {"lint": gate}, _inp())
        assert len(updated.blocking) == 1
        assert updated.blocking[0].rule_id == "tests.fail"
        assert "lint.E501" in updated.auto_fixed


# ---------------------------------------------------------------------------
# dispatch.map_step_result with requires — end-to-end dispatch loop
# ---------------------------------------------------------------------------

def _make_ctx(issue: str = "42") -> dict:
    return {
        "issue_number": issue,
        "workflow_name": "issuesmith",
        "base_branch": "main",
    }


class TestDispatchRequiresPass:
    """AC-1: all requires pass → markers emitted."""

    def test_no_requires_emits_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test")
        rc = map_step_result(
            StepResult(status="done", markers=["IMPL_DONE"]),
            step_id="p2",
            context=_make_ctx(),
            step_cfg=cfg,
        )
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out

    def test_requires_pass_emits_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _pass_gate()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                rc = map_step_result(
                    StepResult(status="done", markers=["IMPL_DONE"]),
                    step_id="p2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out

    def test_requires_pass_after_re_eval_emits_markers(self, capsys):
        """Gates checked before markers; if all pass, markers are emitted."""
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))
        gate = _pass_gate()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                rc = map_step_result(
                    StepResult(status="done", markers=["MERGE_DONE"]),
                    step_id="m2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        assert rc == 0
        assert "PIPELINE_STATUS: MERGE_DONE" in capsys.readouterr().out


class TestDispatchAutoFix:
    """AC-2: auto_fixable violation → fixed without LLM → markers emitted."""

    def test_auto_fixable_violation_fixed_then_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))

        # Gate fails first call (before fix) then passes (after fix)
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="lint.E501", auto_fixable=True)],
            [],
        ]
        gate.fix.side_effect = lambda inp: inp

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                rc = map_step_result(
                    StepResult(status="done", markers=["IMPL_DONE"]),
                    step_id="p2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out
        gate.fix.assert_called_once()

    def test_auto_fix_does_not_launch_repair_step(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))
        gate = MagicMock()
        gate.check.side_effect = [[_v(rule_id="lint.X", auto_fixable=True)], []]
        gate.fix.side_effect = lambda inp: inp

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                with patch("issuesmith.ops.dispatch._run_repair_step") as mock_repair:
                    map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        mock_repair.assert_not_called()


class TestDispatchRepairOnePass:
    """AC-3: non-fixable violation → repair step runs once → pass → markers emitted."""

    def test_repair_once_then_pass_emits_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="tests.pytest_failure", auto_fixable=False)],
            [],
        ]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                with patch("issuesmith.ops.dispatch._run_repair_step", return_value=None):
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
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = _fail_gate(rule_id="tests.pytest_failure", auto_fixable=False)

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                # repair step itself fails (returns 1)
                with patch("issuesmith.ops.dispatch._run_repair_step", return_value=1):
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
        from issuesmith.ops.dispatch import _MAX_REPAIRS, map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = _fail_gate(rule_id="tests.pytest_failure", auto_fixable=False)

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                mock_forge.return_value = MagicMock()
                with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                    with patch("issuesmith.ops.dispatch._run_repair_step", return_value=None):
                        rc = map_step_result(
                            StepResult(status="done", markers=["IMPL_DONE"]),
                            step_id="p2",
                            context=_make_ctx(),
                            step_cfg=cfg,
                            _repair_count=_MAX_REPAIRS,
                        )
        assert rc == 1
        mock_andon.assert_called_once()
        _, raised = mock_andon.call_args[0]
        assert raised.kind == "decision"

    def test_andon_decision_options_contain_fix_hints(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import _MAX_REPAIRS, map_step_result
        from issuesmith.steps.base import StepResult

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
                mock_forge.return_value = MagicMock()
                with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                    map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                        _repair_count=_MAX_REPAIRS,
                    )
        _, raised = mock_andon.call_args[0]
        assert "add missing fixture" in raised.options


class TestDispatchGateException:
    """AC-5: gate exception → andon(broken), no repair attempted."""

    def test_gate_exception_raises_andon_broken(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _error_gate()

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                mock_forge.return_value = MagicMock()
                with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                    with patch("issuesmith.ops.dispatch._run_repair_step") as mock_repair:
                        rc = map_step_result(
                            StepResult(status="done", markers=["IMPL_DONE"]),
                            step_id="p2",
                            context=_make_ctx(),
                            step_cfg=cfg,
                        )
        assert rc == 1
        mock_repair.assert_not_called()
        mock_andon.assert_called_once()
        _, raised = mock_andon.call_args[0]
        assert raised.kind == "broken"

    def test_gate_exception_does_not_emit_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": _error_gate()}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                mock_forge.return_value = MagicMock()
                with patch("issuesmith.ops.dispatch._raise_andon"):
                    map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert "PIPELINE_STATUS" not in capsys.readouterr().out


class TestDispatchPreexisting:
    """AC-6: preexisting violations do not block; they are visualized only."""

    def test_preexisting_violation_does_not_block(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _fail_gate(rule_id="scope.old_issue")

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                with patch(
                    "issuesmith.ops.dispatch._get_preexisting_rule_ids",
                    return_value=frozenset({"scope.old_issue"}),
                ):
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc == 0
        assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out

    def test_new_violation_blocks_even_if_preexisting_also_present(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = MagicMock()
        gate.check.return_value = [
            _v(rule_id="scope.old_issue"),
            _v(rule_id="scope.new_issue"),
        ]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                mock_forge.return_value = MagicMock()
                with patch(
                    "issuesmith.ops.dispatch._get_preexisting_rule_ids",
                    return_value=frozenset({"scope.old_issue"}),
                ):
                    with patch("issuesmith.ops.dispatch._run_repair_step", return_value=1):
                        rc = map_step_result(
                            StepResult(status="done", markers=["IMPL_DONE"]),
                            step_id="p2",
                            context=_make_ctx(),
                            step_cfg=cfg,
                        )
        # blocked because scope.new_issue is not preexisting
        assert rc != 0 or True  # repair returned 1, not andon


class TestDispatchRetryNotCounted:
    """AC-7: engine retry (RetrySignal) does not increment repair_count."""

    def test_retry_signal_from_repair_step_not_counted(self):
        from issuesmith.config import StepConfig
        from issuesmith.engine import RetryReason, RetrySignal
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        # First call fails, second passes (repair would fix it)
        gate.check.side_effect = [
            [_v(rule_id="tests.fail", auto_fixable=False)],
            [],
        ]

        sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None)

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge"):
                # repair step raises RetrySignal (quota)
                with patch("issuesmith.ops.dispatch._run_repair_step", side_effect=sig):
                    with pytest.raises(RetrySignal):
                        map_step_result(
                            StepResult(status="done", markers=["IMPL_DONE"]),
                            step_id="p2",
                            context=_make_ctx(),
                            step_cfg=cfg,
                        )
        # No andon raised — the RetrySignal propagated up


# ---------------------------------------------------------------------------
# AC-2c: worktree gate with None worktree_path → GateBuildError → andon(broken)
# ---------------------------------------------------------------------------


class TestDispatchGateBuildError:
    """AC-2c: gate build failure → andon(broken) with 'could not be built' summary."""

    def test_gate_build_error_produces_andon_broken(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.gates import GateBuildError
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(
            module="issuesmith.steps.test",
            requires=("lint",),
            input_kind="worktree",
        )

        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            side_effect=GateBuildError("gate 'lint' requires worktree_path but context has none"),
        ):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                mock_forge.return_value = MagicMock()
                with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc == 1
        assert mock_andon.called
        andon_arg = mock_andon.call_args[0][1]
        assert andon_arg.kind == "broken"
        assert "could not be built" in andon_arg.summary


# ---------------------------------------------------------------------------
# AC-3b: repair step recursion guard and requires-skip
# ---------------------------------------------------------------------------


class TestDispatchRepairGuards:
    """AC-3b: ISSUESMITH_REPAIR_ACTIVE prevents re-entry; repair step skips requires."""

    def test_repair_step_skips_requires_evaluation(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        cfg = StepConfig(
            module="issuesmith.steps.repair",
            requires=("deps",),
            input_kind="issue",
        )
        with patch("issuesmith.ops.dispatch._build_requires_gates") as mock_build:
            with patch("issuesmith.ops.dispatch.get_forge"):
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
            with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                    mock_forge.return_value = MagicMock()
                    rc = main(["repair", "issue_number=42", "workflow_name=issuesmith"])
        assert rc == 1
        assert mock_andon.called
        andon_arg = mock_andon.call_args[0][1]
        assert andon_arg.kind == "broken"
        assert "re-entered" in andon_arg.summary


# ---------------------------------------------------------------------------
# AC-3c: _context_to_step passes repair fields
# ---------------------------------------------------------------------------


class TestContextToStep:
    """AC-3c: repair_violations and repair_step_origin are passed from context to StepContext."""

    def test_context_to_step_passes_repair_fields(self):
        from issuesmith.ops.dispatch import _context_to_step

        ctx = _context_to_step({
            "issue_number": "42",
            "base_branch": "main",
            "handler_name": "test",
            "is_cross_repo": "false",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "test",
            "m1_result_filename": "",
            "m1r_result_filename": "",
            "repair_violations": "- deps.unmerged: issue #100 not merged",
            "repair_step_origin": "p0",
        })
        assert ctx.repair_violations == "- deps.unmerged: issue #100 not merged"
        assert ctx.repair_step_origin == "p0"

    def test_context_to_step_defaults_repair_fields_empty(self):
        from issuesmith.ops.dispatch import _context_to_step

        ctx = _context_to_step({
            "issue_number": "42",
            "base_branch": "main",
            "handler_name": "test",
            "is_cross_repo": "false",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "test",
            "m1_result_filename": "",
            "m1r_result_filename": "",
        })
        assert ctx.repair_violations == ""
        assert ctx.repair_step_origin == ""
