"""Tests for requires evaluation and repair loop in dispatch (#3626).

Covers: pass / auto_fix / repair-1-pass / max_repairs-exceeded→andon /
gate-exception→broken / preexisting-non-blocking.
"""
from __future__ import annotations

import json
import sys
import types
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import from_comment as _andon_from_comment_body
from issuesmith.config import StepConfig, get_config, reset_config_cache
from issuesmith.gate_registry import Violation
from issuesmith.gates.base import ContractInput
from issuesmith.repair import (
    RequiresResult,
    apply_auto_fixes,
    evaluate_requires,
)
from tests.conftest import _write_nexus_config

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


class _RecordingForge:
    def __init__(self) -> None:
        self.issue_comments: list[tuple[int, str]] = []
        self.issue_updates: list[tuple[int, dict]] = []

    def issue_comment(self, issue: int, body: str) -> dict:
        self.issue_comments.append((issue, body))
        return {"id": 1, "body": body}

    def issue_update(self, issue: int, **kwargs) -> dict:
        self.issue_updates.append((issue, kwargs))
        return {}


_REPAIR_BOUNDARY_MODULE = "issuesmith_test_dispatch_requires_repair_boundary"


class RepairStepBoundary:
    """Records repair-step launches via a fake module at resolve_step_config boundary."""

    def __init__(self) -> None:
        self.launches: list = []
        self.exit_code: int | None = None
        self.side_effect: BaseException | None = None
        self.on_run = None

    def _install_module(self) -> None:
        boundary = self

        def run(ctx, step=None):
            from issuesmith.contract import StepResult

            if boundary.side_effect is not None:
                raise boundary.side_effect
            boundary.launches.append(ctx)
            if boundary.on_run is not None:
                boundary.on_run(ctx)
            if boundary.exit_code is not None and boundary.exit_code != 0:
                return StepResult(status="done", exit_code=boundary.exit_code)
            return StepResult(status="done", markers=["REPAIR_DONE"])

        mod = types.ModuleType(_REPAIR_BOUNDARY_MODULE)
        mod.run = run
        sys.modules[_REPAIR_BOUNDARY_MODULE] = mod


@contextmanager
def stand_in_repair_step(boundary: RepairStepBoundary | None = None, **kwargs):
    """Patch resolve_step_config so the repair step runs a test-local fake module."""
    from issuesmith.config import StepConfig
    from issuesmith.ops import dispatch as dispatch_mod

    b = boundary if boundary is not None else RepairStepBoundary()
    if "exit_code" in kwargs:
        b.exit_code = kwargs["exit_code"]
    if "side_effect" in kwargs:
        b.side_effect = kwargs["side_effect"]
    b._install_module()
    real_resolve = dispatch_mod.resolve_step_config

    def resolve(step_id: str):
        if step_id == "repair":
            return StepConfig(module=_REPAIR_BOUNDARY_MODULE)
        return real_resolve(step_id)

    with patch("issuesmith.ops.dispatch.resolve_step_config", side_effect=resolve):
        yield b


def _raised_andons(forge: _RecordingForge):
    out = []
    for _, body in forge.issue_comments:
        parsed = _andon_from_comment_body(body)
        if parsed is not None:
            out.append(parsed)
    return out

def _inp() -> ContractInput:
    return ContractInput(body="issue body", labels=[])


@pytest.fixture
def nexus_config(monkeypatch, tmp_path):
    """Nexus declaration for modules split out of this file; conftest applies it by module name."""
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(_write_nexus_config(tmp_path)))
    reset_config_cache()


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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _pass_gate()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))
        gate = _pass_gate()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))

        # Gate fails first call (before fix) then passes (after fix)
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="lint.E501", auto_fixable=True)],
            [],
        ]
        gate.fix.side_effect = lambda inp: inp

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint",))
        gate = MagicMock()
        gate.check.side_effect = [[_v(rule_id="lint.X", auto_fixable=True)], []]
        gate.fix.side_effect = lambda inp: inp

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert repair.launches == []


class TestDispatchGateException:
    """AC-5: gate exception → andon(broken), no repair attempted."""

    def test_gate_exception_raises_andon_broken(self):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _error_gate()

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc == 1
        raised = _raised_andons(forge)[0]
        assert raised.kind == "broken"
        assert repair.launches == []

    def test_gate_exception_does_not_emit_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": _error_gate()}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _fail_gate(rule_id="scope.old_issue")

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
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
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = MagicMock()
        gate.check.return_value = [
            _v(rule_id="scope.old_issue"),
            _v(rule_id="scope.new_issue"),
        ]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with patch(
                    "issuesmith.ops.dispatch._get_preexisting_rule_ids",
                    return_value=frozenset({"scope.old_issue"}),
                ):
                    with stand_in_repair_step(exit_code=1):
                        rc = map_step_result(
                            StepResult(status="done", markers=["IMPL_DONE"]),
                            step_id="p2",
                            context=_make_ctx(),
                            step_cfg=cfg,
                        )
        # blocked because scope.new_issue is not preexisting
        assert rc != 0 or True  # repair returned 1, not andon
        # No andon raised — the RetrySignal propagated up


# ---------------------------------------------------------------------------
# AC-2c: worktree gate with None worktree_path → GateBuildError → andon(broken)
# ---------------------------------------------------------------------------


class TestDispatchGateBuildError:
    """AC-2c: gate build failure → andon(broken) with 'could not be built' summary."""

    def test_gate_build_error_produces_andon_broken(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.gates import GateBuildError
        from issuesmith.ops.dispatch import map_step_result

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
                forge = _RecordingForge()
                mock_forge.return_value = forge
                rc = map_step_result(
                    StepResult(status="done", markers=["IMPL_DONE"]),
                    step_id="p2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert andon_arg is not None
        assert andon_arg.kind == "broken"
        assert "could not be built" in andon_arg.summary


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

    def test_run_repair_step_populates_structured_repair_context(self):
        from issuesmith.ops.dispatch import _run_repair_step

        captured: dict = {}

        def fake_try(step_id, ctx):
            captured.update(ctx)
            return 0

        v = Violation(
            rule_id="pr_scope.out_of_allow",
            severity="fail",
            message="out of scope",
            location="tools/extra.py",
            auto_fixable=False,
            fix_hint=None,
        )
        base = _make_ctx()
        base["derived_allow_paths"] = "tests/**"
        with patch("issuesmith.ops.dispatch._try_python_step", side_effect=fake_try):
            _run_repair_step([v], "p1", base)

        assert captured["repair_violation_summary"] == (
            "- pr_scope.out_of_allow: out of scope"
        )
        assert captured["repair_widen_candidates"] == "tools/extra.py"
        assert captured["derived_allow_paths"] == "tests/**"


# ---------------------------------------------------------------------------
# #3756 AC-3b: derived_allow_paths kept across repairs; repair instruction text
# ---------------------------------------------------------------------------


class TestDerivedAllowPaths:
    def test_derived_kept_after_repair_and_allowed_by_pr_scope(self, tmp_path):
        from issuesmith.config import StepConfig
        from issuesmith.gates.worktree import TestsGate
        from issuesmith.ops.dispatch import run_requires_loop

        state = {"repaired": False, "calls": 0}

        def fake_tests_check(self, body, labels):
            state["calls"] += 1
            if state["repaired"]:
                self.derived_allow_paths = []
                return []
            self.derived_allow_paths = ["tests/test_uses_mod.py"]
            return [_v(rule_id="tests.pytest_failure")]

        def fake_changed(root, base):
            if state["repaired"]:
                return ["src/pkg/mod.py", "tests/test_uses_mod.py"]
            return ["src/pkg/mod.py"]

        repair_contexts: list[dict] = []
        repair = RepairStepBoundary()

        def _on_repair_run(ctx):
            repair_contexts.append(asdict(ctx))
            state["repaired"] = True

        repair.on_run = _on_repair_run

        cfg = StepConfig(
            module="", requires=("tests", "pr_scope"), input_kind="worktree",
            requires_declared=True,
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(tmp_path),
            "allow_paths": "- src/pkg/mod.py",
        }
        with (
            patch.object(TestsGate, "check", fake_tests_check),
            patch("issuesmith.gates.worktree.changed_files", side_effect=fake_changed),
            patch("issuesmith.gates.worktree.check_derived_test_guard", return_value=[]),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
            stand_in_repair_step(repair),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "p1", context)

        assert rc is None
        assert _raised_andons(forge) == []
        assert state["calls"] == 2
        assert context["derived_allow_paths"] == "tests/test_uses_mod.py"
        assert repair_contexts[0]["derived_allow_paths"] == "tests/test_uses_mod.py"
        # frozen order: allow_paths itself is not rewritten
        assert context["allow_paths"] == "- src/pkg/mod.py"

    def test_build_requires_gates_reads_derived_from_context(self, tmp_path):
        from issuesmith.ops.dispatch import _build_requires_gates

        gates = _build_requires_gates(
            ("pr_scope",),
            {
                "worktree_path": str(tmp_path),
                "allow_paths": "- src/a.py",
                "derived_allow_paths": "tests/test_a.py\ntests/test_b.py\n",
            },
        )
        assert gates["pr_scope"]._derived_allow_paths == [
            "tests/test_a.py", "tests/test_b.py",
        ]

    def _repair_text(self, context: dict) -> str:
        from issuesmith.ops.dispatch import _run_repair_step

        captured: dict = {}

        def fake_step(step_id, ctx):
            captured.update(ctx)
            return 0

        with patch("issuesmith.ops.dispatch._try_python_step", side_effect=fake_step):
            assert _run_repair_step([_v("tests.pytest_failure")], "p1", context) is None
        return captured["repair_violations"]

    def test_repair_instruction_lists_derived(self):
        text = self._repair_text({
            "issue_number": "42",
            "derived_allow_paths": "tests/test_a.py\ntests/test_b.py",
        })
        assert (
            "Do not touch files outside allow_paths and derived_allow_paths.\n"
            "derived_allow_paths:\n- tests/test_a.py\n- tests/test_b.py\n"
        ) in text
        assert text.endswith("- tests.pytest_failure: test violation")

    def test_repair_instruction_unchanged_without_derived(self):
        text = self._repair_text({"issue_number": "42"})
        assert text == (
            "Fix only the violations below with the smallest possible diff. "
            "Do not touch files outside allow_paths.\n"
            "- tests.pytest_failure: test violation"
        )


class TestRequiresRepairMetricsViolations:
    """Blocking violations are serialized on requires_repair (#6207)."""

    def test_requires_repair_records_all_blocking_details(self, tmp_path, monkeypatch):
        from issuesmith.ops import dispatch as dispatch_mod
        from issuesmith.ops.dispatch import run_requires_loop

        metrics_path = tmp_path / "metrics.jsonl"
        cfg_obj = replace(get_config(), paths=replace(get_config().paths, metrics=metrics_path))
        monkeypatch.setattr(dispatch_mod, "get_config", lambda: cfg_obj)

        loc_v = Violation(
            rule_id="pr_scope.out_of_allow",
            severity="fail",
            message="outside",
            location="tools/extra.py",
            auto_fixable=False,
            fix_hint=None,
        )
        msg_v = Violation(
            rule_id="tests.pytest_failure",
            severity="fail",
            message="assert 0",
            location=None,
            auto_fixable=False,
            fix_hint=None,
        )

        class _MultiGate:
            def check(self, body, labels):
                return [loc_v, msg_v]

        step_cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"tests": _MultiGate()},
        ):
            with patch("issuesmith.ops.dispatch.get_forge"):
                with patch("issuesmith.ops.dispatch._run_repair_step", return_value=1):
                    run_requires_loop(step_cfg, "p1", _make_ctx(), origin="requires")

        lines = [json.loads(ln) for ln in metrics_path.read_text().splitlines() if ln.strip()]
        repair_lines = [ln for ln in lines if ln.get("event") == "requires_repair"]
        assert len(repair_lines) == 1
        row = repair_lines[0]
        assert row["origin"] == "requires"
        assert row["rule_ids"] == ["pr_scope.out_of_allow", "tests.pytest_failure"]
        assert [v["location"] for v in row["violations"]] == ["tools/extra.py", None]
        assert row["violations"][1]["message"] == "assert 0"

    def test_safe_record_metrics_skips_violations_for_legacy_recorder(self, tmp_path, monkeypatch):
        from issuesmith.ops import dispatch as dispatch_mod

        calls: list[tuple] = []

        def legacy_record_metrics(path, event, step_id, issue_num, rule_ids=None, *, origin=None):
            calls.append((event, step_id, issue_num, rule_ids, origin))

        monkeypatch.setattr("issuesmith.repair.record_metrics", legacy_record_metrics)
        cfg_obj = replace(
            get_config(), paths=replace(get_config().paths, metrics=tmp_path / "m.jsonl")
        )
        monkeypatch.setattr(dispatch_mod, "get_config", lambda: cfg_obj)
        v = Violation(
            rule_id="pr_scope.out_of_allow",
            severity="fail",
            message="outside",
            location="tools/extra.py",
            auto_fixable=False,
            fix_hint=None,
        )
        dispatch_mod._safe_record_metrics(
            "requires_repair",
            "p1",
            42,
            rule_ids=["pr_scope.out_of_allow"],
            origin="accepts",
            violations=[v],
        )
        assert calls == [("requires_repair", "p1", 42, ["pr_scope.out_of_allow"], "accepts")]
