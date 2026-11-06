"""dispatch requires: repair oscillation, repair group notes and pass summaries."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from ghdag.workflow.gates import Violation

from tests import test_dispatch_requires as _base
from tests.test_dispatch_requires import (
    RepairStepBoundary,
    _make_ctx,
    _pass_gate,
    _raised_andons,
    _RecordingForge,
    _v,
    stand_in_repair_step,
)

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")


# ---------------------------------------------------------------------------
# #4285: repair oscillation detection
# ---------------------------------------------------------------------------


class TestRepairOscillation:
    """Repair loop stops early when violations do not improve after a repair."""

    def test_same_violations_after_repair_raises_oscillation_andon(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="tests.pytest_failure", auto_fixable=False)],
            [_v(rule_id="tests.pytest_failure", auto_fixable=False)],
        ]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert andon_arg.kind == "decision"
        assert "repair oscillation detected" in andon_arg.summary
        assert "step p1" in andon_arg.summary
        assert "after 1 repair(s)" in andon_arg.summary
        assert "tests.pytest_failure" in andon_arg.summary

    def test_improvement_after_repair_continues_loop(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(
            module="issuesmith.steps.test",
            requires=("lint", "tests"),
        )
        gate = MagicMock()
        gate.check.side_effect = [
            [
                _v(rule_id="lint.E501", auto_fixable=False),
                _v(rule_id="tests.pytest_failure", auto_fixable=False),
            ],
            [_v(rule_id="tests.pytest_failure", auto_fixable=False)],
            [],
        ]

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc is None
        assert _raised_andons(forge) == []
        assert "PIPELINE_STATUS: IMPL_DONE" not in capsys.readouterr().out
        assert len(repair.launches) == 2

    def test_worsened_violations_after_repair_raises_oscillation_andon(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(
            module="issuesmith.steps.test",
            requires=("lint", "tests"),
        )
        gate = MagicMock()
        gate.check.side_effect = [
            [_v(rule_id="lint.E501", auto_fixable=False)],
            [
                _v(rule_id="lint.E501", auto_fixable=False),
                _v(rule_id="tests.pytest_failure", auto_fixable=False),
            ],
        ]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert "repair oscillation detected" in andon_arg.summary

    def test_oscillation_with_pr_scope_includes_widen_option(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("pr_scope",))
        violation = Violation(
            rule_id="pr_scope.out_of_allow",
            severity="fail",
            message="scripts/x.py: outside allow_paths",
            location="scripts/x.py",
            auto_fixable=False,
            fix_hint="widen:scripts/x.py",
        )
        gate = MagicMock()
        gate.check.side_effect = [[violation], [violation]]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"pr_scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert any(
            o.startswith("widen:") and "scripts/x.py" in o for o in andon_arg.options
        )
        assert "split" in andon_arg.options
        assert "reject" in andon_arg.options

    def test_different_tests_same_rule_id_continues_repair(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_pytest_failure_v("tests/a.py::test_a")],
            [
                _pytest_failure_v("tests/b.py::test_b"),
                _pytest_failure_v("tests/c.py::test_c"),
            ],
            [],
        ]

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc is None
        assert _raised_andons(forge) == []
        assert len(repair.launches) == 2

    def test_same_file_different_test_is_progress(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_pytest_failure_v("tests/a.py::test_a")],
            [_pytest_failure_v("tests/a.py::test_a2")],
            [],
        ]

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc is None
        assert _raised_andons(forge) == []
        assert len(repair.launches) == 2

    def test_identical_pytest_fingerprint_raises_oscillation(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        violation = _pytest_failure_v("tests/a.py::test_a")
        gate = MagicMock()
        gate.check.side_effect = [[violation], [violation]]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert "repair oscillation detected" in andon_arg.summary

    def test_partial_resolution_continues_repair(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        a = _pytest_failure_v("tests/a.py::test_a")
        b = _pytest_failure_v("tests/b.py::test_b")
        gate = MagicMock()
        gate.check.side_effect = [[a, b], [a], []]

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc is None
        assert _raised_andons(forge) == []
        assert len(repair.launches) == 2

    def test_added_failure_with_same_fingerprint_raises_oscillation(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        a = _pytest_failure_v("tests/a.py::test_a")
        b = _pytest_failure_v("tests/b.py::test_b")
        gate = MagicMock()
        gate.check.side_effect = [[a], [a, b]]

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step():
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert "repair oscillation detected" in andon_arg.summary

    def test_always_new_failures_hits_max_repairs_not_oscillation(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("tests",))
        gate = MagicMock()
        gate.check.side_effect = [
            [_pytest_failure_v("tests/a.py::test_a")],
            [_pytest_failure_v("tests/b.py::test_b")],
            [_pytest_failure_v("tests/c.py::test_c")],
            [_pytest_failure_v("tests/d.py::test_d")],
        ]

        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with stand_in_repair_step(repair):
                    rc = run_requires_loop(cfg, "p1", _make_ctx())

        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert "requires evaluation failed after 3 repair(s)" in andon_arg.summary
        assert "repair oscillation" not in andon_arg.summary
        assert len(repair.launches) == 3


# ---------------------------------------------------------------------------
# #4337: group note for shared pytest failure root cause in repair prompt
# ---------------------------------------------------------------------------


def _pytest_v(message: str) -> Violation:
    return Violation(
        rule_id="tests.pytest_failure",
        severity="fail",
        message=message,
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )


def _pytest_failure_v(test_id: str, summary: str = "err") -> Violation:
    """Build a tests.pytest_failure violation like TestsGate (location + FAILED line)."""
    location = test_id.split("::", 1)[0] if "::" in test_id else None
    return Violation(
        rule_id="tests.pytest_failure",
        severity="fail",
        message=f"FAILED {test_id} - {summary}",
        location=location,
        auto_fixable=False,
        fix_hint=None,
    )


class TestRepairGroupNote:
    """Repair prompt includes a grouping note when 2+ pytest failures share an exception class."""

    def _repair_text(self, violations: list[Violation], context: dict | None = None) -> str:
        from issuesmith.ops.dispatch import _run_repair_step

        captured: dict = {}

        def fake_step(step_id, ctx):
            captured.update(ctx)
            return 0

        ctx = context or {"issue_number": "42"}
        with patch("issuesmith.ops.dispatch._try_python_step", side_effect=fake_step):
            assert _run_repair_step(violations, "p1", ctx) is None
        return captured["repair_violations"]

    def test_group_note_when_same_exception_class(self):
        violations = [
            _pytest_v("WriteGuardError: side effect outside tmp: a"),
            _pytest_v("WriteGuardError: side effect outside tmp: b"),
            _pytest_v("WriteGuardError: side effect outside tmp: c"),
        ]
        text = self._repair_text(violations)
        assert (
            "Note: 3 tests share the same root cause (WriteGuardError). "
            "Fix all 3 of them in a single commit before running the test suite.\n"
        ) in text

    def test_no_group_note_for_single_pytest_failure(self):
        text = self._repair_text([_pytest_v("WriteGuardError: side effect outside tmp")])
        assert "share the same root cause" not in text

    def test_no_group_note_when_exception_classes_differ(self):
        violations = [
            _pytest_v("WriteGuardError: side effect outside tmp"),
            _pytest_v("AssertionError: expected 1 == 2"),
        ]
        text = self._repair_text(violations)
        assert "share the same root cause" not in text

    def test_no_group_note_for_non_pytest_violations(self):
        text = self._repair_text([
            _v(rule_id="lint.E501", auto_fixable=False),
            _v(rule_id="pr_scope.out_of_allow", auto_fixable=False),
        ])
        assert "share the same root cause" not in text

    def test_group_note_uses_largest_exception_class_group(self):
        violations = [
            _pytest_v("WriteGuardError: side effect outside tmp: a"),
            _pytest_v("WriteGuardError: side effect outside tmp: b"),
            _pytest_v("AssertionError: expected 1 == 2"),
        ]
        text = self._repair_text(violations)
        assert (
            "Note: 2 tests share the same root cause (WriteGuardError). "
            "Fix all 2 of them in a single commit before running the test suite.\n"
        ) in text
        assert "AssertionError" not in text.split("share the same root cause")[0]

    def test_group_note_unit_no_match(self):
        from issuesmith.ops.dispatch import _group_note

        assert _group_note([_pytest_v("failed without a class name")]) == ""

    def test_group_note_unit_ignores_non_pytest_failures(self):
        from issuesmith.ops.dispatch import _group_note

        assert _group_note([
            _v(rule_id="lint.E501"),
            _pytest_v("WriteGuardError: side effect outside tmp"),
        ]) == ""


class TestPassSummary:
    def test_format_pass_summary_counts_per_gate(self):
        from issuesmith.ops.dispatch import _format_pass_summary

        gates = {"lint": object(), "tests": object(), "external_leak": object()}
        line = _format_pass_summary(
            "p1", gates, [_v("tests.failed"), _v("tests.other"), _v("unknown.rule")]
        )
        assert line == "[requires] p1 pass: lint=0 tests=2 external_leak=0"

    def test_format_pass_summary_all_zero(self):
        from issuesmith.ops.dispatch import _format_pass_summary

        line = _format_pass_summary("p1", {"lint": object(), "tests": object()}, [])
        assert line == "[requires] p1 pass: lint=0 tests=0"

    def test_run_requires_loop_logs_pass_summary(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("lint", "external_leak"))
        gates = {"lint": _pass_gate(), "external_leak": _pass_gate()}
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value=gates), \
             patch("issuesmith.ops.dispatch.get_forge"):
            rc = run_requires_loop(cfg, "p1", _make_ctx())
        assert rc is None
        err_lines = capsys.readouterr().err.splitlines()
        hits = [ln for ln in err_lines if ln.startswith("[requires] p1 pass:")]
        assert hits == ["[requires] p1 pass: lint=0 external_leak=0"]
