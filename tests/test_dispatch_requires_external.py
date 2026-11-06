"""dispatch requires: external gates, unmerged deps and forbidden paths."""

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
    stand_in_repair_step,
)

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")

# ---------------------------------------------------------------------------
# nexus #4116: fail-closed external_leak target + requires observability
# ---------------------------------------------------------------------------


class TestExternalGateRequires:
    def test_build_requires_gates_external_gate(self, tmp_path, monkeypatch):
        from issuesmith.ops.dispatch import _build_requires_gates

        mod_name = "mypkg_gates_dispatch"
        gate_ref = f"{mod_name}:MY_GATE"

        class _G:
            def check(self, body, labels):
                return []

        (tmp_path / f"{mod_name}.py").write_text(
            """
from issuesmith.gates import GateEntry

class _G:
    def check(self, body, labels):
        return []

MY_GATE = GateEntry(
    input_kind="worktree",
    build=lambda ctx: _G(),
)
""",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        gates = _build_requires_gates(
            (gate_ref,),
            {"worktree_path": str(tmp_path), "allow_paths": "- x.py", "base_branch": "main"},
        )
        assert gate_ref in gates
        assert gates[gate_ref].check("body", []) == []

    def test_build_requires_gates_unresolved_raises_gate_build_error(self, tmp_path, monkeypatch):
        from issuesmith.gates import GateBuildError
        from issuesmith.ops.dispatch import _build_requires_gates

        ref = "mypkg_gates_dispatch_fail:MISSING"
        (tmp_path / "mypkg_gates_dispatch_fail.py").write_text("pass\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(GateBuildError, match=ref):
            _build_requires_gates((ref,), {"worktree_path": str(tmp_path)})

    def test_is_violation_repairable_external_non_repairable(self, tmp_path, monkeypatch):
        from issuesmith.ops.dispatch import _is_violation_repairable

        mod_name = "mypkg_gates_repair"
        gate_ref = f"{mod_name}:MY_GATE"
        (tmp_path / f"{mod_name}.py").write_text(
            """
from issuesmith.gates import GateEntry

class _G:
    def check(self, body, labels):
        return []

MY_GATE = GateEntry(
    input_kind="worktree",
    build=lambda ctx: _G(),
    repairable=False,
)


""",
            encoding="utf-8",
        )
        monkeypatch.syspath_prepend(str(tmp_path))
        assert (
            _is_violation_repairable(f"{gate_ref}.rule", {gate_ref: object()}, "p1")
            is False
        )


class TestDepsUnmergedRequires:
    def test_unmerged_deferred_when_phase_has_deps_terminal(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="", requires=("deps",))
        gate = _fail_gate("deps.unmerged")
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"deps": gate},
            ),
            patch("issuesmith.ops.dispatch._deps_gated_by_admission", return_value=True),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "b1", _make_ctx())
        assert rc is None
        assert _raised_andons(forge) == []
        assert "deps.unmerged deferred to phase admission" in capsys.readouterr().err
        assert repair.launches == []

    def test_unmerged_raises_andon_when_not_gated_by_admission(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="", requires=("deps",))
        gate = _fail_gate("deps.unmerged")
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"deps": gate},
            ),
            patch("issuesmith.ops.dispatch._deps_gated_by_admission", return_value=False),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "repair", _make_ctx())
        assert rc == 1
        assert _raised_andons(forge)[0].kind == "decision"
        assert repair.launches == []

    def test_unparsed_dependency_section_still_triggers_repair(self):
        from issuesmith.config import RepairConfig, StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(
            module="", requires=("deps",), repair=RepairConfig(max=1)
        )
        gate = _fail_gate("deps.unparsed_dependency_section")
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"deps": gate},
            ),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            run_requires_loop(cfg, "b1", _make_ctx())
        assert len(repair.launches) == 1


class TestTargetUnknownNotRepairable:
    def test_is_violation_repairable_excludes_target_unknown(self):
        from issuesmith.ops.dispatch import _is_violation_repairable

        gates = {"external_leak": object()}
        assert _is_violation_repairable(
            "external_leak.target_unknown", gates, "p1"
        ) is False
        assert _is_violation_repairable(
            "external_leak.cjk_added_line", gates, "p1"
        ) is True

    def test_target_unknown_raises_decision_andon_without_repair(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="issuesmith.steps.test", requires=("external_leak",))
        gate = _fail_gate("external_leak.target_unknown")
        repair = RepairStepBoundary()
        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"external_leak": gate}), \
             patch("issuesmith.ops.dispatch.get_forge") as mock_forge, \
             stand_in_repair_step(repair):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "p1", _make_ctx())
        assert rc == 1
        assert _raised_andons(forge)[0].kind == "decision"
        assert repair.launches == []


class TestForbiddenPathPrScopeRequires:
    def test_is_violation_repairable_forbidden_vs_out_of_allow(self):
        from issuesmith.gates import GATE_REGISTRY
        from issuesmith.ops.dispatch import _is_violation_repairable

        gates = {"pr_scope": object()}
        assert (
            _is_violation_repairable("pr_diff_scope.forbidden_path", gates, "p1")
            is False
        )
        assert (
            _is_violation_repairable("pr_scope.out_of_allow", gates, "p1")
            is True
        )
        assert GATE_REGISTRY["pr_scope"].repairable is True

    def test_review_engine_error_decision_andon_without_repair(self):
        from issuesmith.config import RepairConfig, StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        v = Violation(
            rule_id="review.cp2.engine_error",
            severity="fail",
            message="review engine exited 1: quota",
            location=None,
            auto_fixable=False,
            fix_hint="review engine exited 1: quota",
        )
        gate = MagicMock()
        gate.check.return_value = [v]
        recorded: list[str] = []

        def _record(metric: str, *args, **kwargs) -> None:
            recorded.append(metric)

        cfg = StepConfig(module="", requires=("review",), repair=RepairConfig(max=2))
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"review": gate},
            ),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
            patch("issuesmith.ops.dispatch._safe_record_metrics", side_effect=_record),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "cp2", _make_ctx())
        assert rc == 1
        assert "requires_repair" not in recorded
        andon_arg = _raised_andons(forge)[0]
        assert andon_arg.kind == "decision"
        assert repair.launches == []

    def test_forbidden_only_decision_andon_without_repair_or_metrics(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        path = "docs/lessons/issuesmith.jsonl"
        v = Violation(
            rule_id="pr_diff_scope.forbidden_path",
            severity="fail",
            message=f"forbidden path: {path} (matched '[!s]*.jsonl')",
            location=path,
            auto_fixable=False,
            fix_hint=f"git checkout main -- {path}",
        )
        gate = MagicMock()
        gate.check.return_value = [v]
        recorded: list[str] = []

        def _record(metric: str, *args, **kwargs) -> None:
            recorded.append(metric)

        cfg = StepConfig(module="", requires=("pr_scope",))
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"pr_scope": gate},
            ),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
            patch("issuesmith.ops.dispatch._safe_record_metrics", side_effect=_record),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "p1", _make_ctx())
        assert rc == 1
        assert "requires_repair" not in recorded
        andon_arg = _raised_andons(forge)[0]
        assert andon_arg.kind == "decision"
        assert andon_arg.options == ["split", "reject"]
        assert "forbidden path:" in andon_arg.summary
        assert path in andon_arg.summary
        assert repair.launches == []

    def test_mixed_forbidden_and_out_of_allow_widen_only_out_of_allow(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        forbidden_path = "docs/lessons/issuesmith.jsonl"
        out_path = "scripts/x.py"
        gate = MagicMock()
        gate.check.return_value = [
            Violation(
                rule_id="pr_diff_scope.forbidden_path",
                severity="fail",
                message=f"forbidden path: {forbidden_path}",
                location=forbidden_path,
                auto_fixable=False,
                fix_hint=f"git checkout main -- {forbidden_path}",
            ),
            Violation(
                rule_id="pr_scope.out_of_allow",
                severity="fail",
                message=f"{out_path}: outside allow_paths",
                location=out_path,
                auto_fixable=False,
                fix_hint=f"widen:{out_path}",
            ),
        ]
        cfg = StepConfig(module="", requires=("pr_scope",))
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"pr_scope": gate},
            ),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            run_requires_loop(cfg, "p1", _make_ctx())
        andon_arg = _raised_andons(forge)[0]
        widen_opts = [o for o in andon_arg.options if o.startswith("widen:")]
        assert widen_opts == [f"widen:{out_path}"]
        assert forbidden_path not in widen_opts[0]
        assert repair.launches == []


class TestFetchFreshIssueBodyLogging:
    def test_forge_failure_without_context_body_logs(self, capsys):
        from issuesmith.ops.dispatch import _fetch_fresh_issue_body

        forge = MagicMock()
        forge.issue_get.side_effect = RuntimeError("403")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            body = _fetch_fresh_issue_body({"issue_number": "7"})
        assert body == ""
        err_lines = capsys.readouterr().err.splitlines()
        hits = [
            ln for ln in err_lines
            if ln.startswith("[requires] issue body unavailable (issue=7, reason=RuntimeError)")
        ]
        assert len(hits) == 1

    def test_forge_failure_with_context_body_is_silent(self, capsys):
        from issuesmith.ops.dispatch import _fetch_fresh_issue_body

        forge = MagicMock()
        forge.issue_get.side_effect = RuntimeError("403")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            body = _fetch_fresh_issue_body({"issue_number": "7", "issue_body": "cached"})
        assert body == "cached"
        assert "issue body unavailable" not in capsys.readouterr().err

    def test_empty_forge_body_without_context_logs_empty_body(self, capsys):
        from issuesmith.ops.dispatch import _fetch_fresh_issue_body

        forge = MagicMock()
        forge.issue_get.return_value = {"body": ""}
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            body = _fetch_fresh_issue_body({"issue_number": "7"})
        assert body == ""
        assert "(issue=7, reason=empty_body)" in capsys.readouterr().err
