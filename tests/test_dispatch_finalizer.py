"""Tests for dispatch label projection, the step label guard and broken-andon on template
failure (#3509 / #4807)."""
from __future__ import annotations

import dataclasses
import sys
import types
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

import issuesmith.config as config_module
from issuesmith.config import PhaseConfig
from issuesmith.contract import StepResult
from issuesmith.ops import dispatch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _context(issue_number: str = "42", workflow_name: str = "issuesmith") -> dict[str, str]:
    return {
        "issue_number": issue_number,
        "workflow_name": workflow_name,
        "base_branch": "main",
        "handler_name": "impl",
        "is_cross_repo": "false",
        "target_clone_path": "",
        "source": "",
        "m1_result_filename": "",
        "m1r_result_filename": "",
    }


def _mock_forge():
    forge = MagicMock()
    forge.issue_update.return_value = {}
    forge.issue_comment.return_value = {}
    return forge


class _LabelForge:
    """In-memory forge holding one Issue's labels; records every issue_update."""

    def __init__(self, labels: set[str]) -> None:
        self.labels = set(labels)
        self.updates: list[dict[str, Any]] = []
        self.comments: list[str] = []

    def issue_get(self, number: int, fields: list[str] | None = None) -> dict[str, Any]:
        return {"number": number, "labels": [{"name": lb} for lb in sorted(self.labels)]}

    def issue_update(self, number: int, **kwargs: Any) -> None:
        self.updates.append(kwargs)
        self.labels |= set(kwargs.get("labels_add") or [])
        self.labels -= set(kwargs.get("labels_remove") or [])

    def issue_comment(self, number: int, body: str) -> None:
        self.comments.append(body)


def _ns() -> str:
    return config_module.get_config().label_namespace


@pytest.fixture
def phased(monkeypatch):
    """Config whose develop phase runs (p0, cp2) and merge phase runs (m1, m2)."""

    def _apply(guard: str = "warn", develop_preconditions: tuple[str, ...] = ()):
        cfg = dataclasses.replace(
            config_module.get_config(),
            phases=(
                PhaseConfig(name="draft", role="design", entry_step="b1"),
                PhaseConfig(
                    name="develop",
                    role="implementation",
                    entry_step="cp2",
                    preconditions=develop_preconditions,
                    steps=("p0", "cp2"),
                ),
                PhaseConfig(
                    name="merge", role="implementation", entry_step="m2", steps=("m1", "m2")
                ),
            ),
            label_write_guard=guard,
        )
        monkeypatch.setattr(config_module, "_cached", cfg)
        return cfg

    return _apply


# ---------------------------------------------------------------------------
# StepResult(done): only the final step of a phase projects <phase>-done
# ---------------------------------------------------------------------------

class TestFinalStepDoneProjection:
    def test_final_step_done_applies_phase_done_in_one_update(self, phased):
        phased()
        ns = _ns()
        forge = _LabelForge({f"{ns}:merge-running", "scope:milestone"})
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="m2", context=_context())
        assert rc == 0
        assert forge.updates == [
            {"labels_add": [f"{ns}:merge-done"], "labels_remove": [f"{ns}:merge-running"]}
        ]
        assert forge.labels == {f"{ns}:merge-done", "scope:milestone"}

    def test_non_final_step_done_writes_nothing(self, phased):
        phased()
        forge = _mock_forge()
        result = StepResult(status="done", markers=["M1_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="m1", context=_context())
        assert rc == 0
        forge.issue_get.assert_not_called()
        forge.issue_update.assert_not_called()

    def test_marker_does_not_decide_the_phase(self, phased):
        """MERGE_DONE from a non-final step projects nothing (the marker table is gone)."""
        phased()
        forge = _mock_forge()
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            dispatch.map_step_result(result, step_id="m1", context=_context())
        forge.issue_update.assert_not_called()

    def test_step_outside_phases_writes_nothing(self, phased):
        phased()
        forge = _mock_forge()
        result = StepResult(status="done", markers=["WORKTREE_READY"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="p3", context=_context())
        assert rc == 0
        forge.issue_update.assert_not_called()

    def test_old_api_exit_zero_follows_the_same_rule(self, phased):
        phased(develop_preconditions=("draft-done",))
        ns = _ns()
        forge = _LabelForge({f"{ns}:develop-running", f"{ns}:draft-done"})
        result = StepResult(exit_code=0, pipeline_status="IMPL_DONE")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="cp2", context=_context())
        assert rc == 0
        assert forge.labels == {f"{ns}:develop-done", f"{ns}:draft-done"}

    def test_old_api_non_final_step_writes_nothing(self, phased):
        phased()
        forge = _mock_forge()
        result = StepResult(exit_code=0, pipeline_status="WORKTREE_READY")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            dispatch.map_step_result(result, step_id="p0", context=_context())
        forge.issue_update.assert_not_called()

    def test_old_api_failure_writes_nothing(self, phased):
        phased()
        forge = _mock_forge()
        result = StepResult(exit_code=1, pipeline_status="IMPL_FAILED")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="cp2", context=_context())
        assert rc == 1
        forge.issue_update.assert_not_called()

    def test_default_config_m2_projects_merge_done(self):
        ns = _ns()
        forge = _LabelForge({f"{ns}:merge-running"})
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            dispatch.map_step_result(result, step_id="m2", context=_context())
        assert forge.labels == {f"{ns}:merge-done"}

    def test_projection_does_not_fail_on_forge_error(self, phased):
        phased()
        forge = _mock_forge()
        forge.issue_get.return_value = {"labels": [{"name": f"{_ns()}:merge-running"}]}
        forge.issue_update.side_effect = RuntimeError("network error")
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="m2", context=_context())
        assert rc == 0  # warning printed but no crash

    def test_projection_skipped_when_no_issue_number(self, phased):
        phased()
        forge = _mock_forge()
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = dispatch.map_step_result(result, step_id="m2", context=_context(issue_number=""))
        assert rc == 0
        forge.issue_get.assert_not_called()
        forge.issue_update.assert_not_called()

    def test_custom_namespace_used(self, monkeypatch):
        custom = dataclasses.replace(config_module.get_config(), label_namespace="myns")
        monkeypatch.setattr(config_module, "_cached", custom)
        forge = _LabelForge({"myns:merge-running"})
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            dispatch.map_step_result(result, step_id="m2", context=_context())
        assert forge.labels == {"myns:merge-done"}


# ---------------------------------------------------------------------------
# Step start: andon / waiting cleared, the step's phase running
# ---------------------------------------------------------------------------

class TestStepStartProjection:
    def _start(self, forge, step_id: str) -> int:
        with (
            patch("issuesmith.ops.dispatch.get_forge", return_value=forge),
            patch("issuesmith.ops.dispatch._try_python_step", return_value=0),
        ):
            return dispatch.main([step_id, "issue_number=42", "workflow_name=issuesmith"])

    def test_recover_rerun_converges_to_running(self, phased):
        """dag recover: andon set, running gone -> the re-run shows only <phase>-running."""
        phased()
        ns = _ns()
        forge = _LabelForge({f"{ns}:andon-broken", f"{ns}:develop-ready"})
        assert self._start(forge, "p0") == 0
        assert forge.labels == {f"{ns}:develop-running"}
        assert len(forge.updates) == 1

    def test_start_keeps_preconditions_and_unmanaged_labels(self, phased):
        phased(develop_preconditions=("draft-done",))
        ns = _ns()
        forge = _LabelForge({f"{ns}:draft-done", f"{ns}:waiting", "scope:milestone"})
        self._start(forge, "cp2")
        assert forge.labels == {f"{ns}:develop-running", f"{ns}:draft-done", "scope:milestone"}

    def test_step_outside_phases_only_clears_andon_and_waiting(self, phased):
        phased()
        ns = _ns()
        forge = _LabelForge({f"{ns}:merge-ready", f"{ns}:andon-blocked", f"{ns}:waiting"})
        self._start(forge, "p3")
        assert forge.labels == {f"{ns}:merge-ready"}

    def test_aligned_issue_is_not_written(self, phased):
        phased()
        ns = _ns()
        forge = _LabelForge({f"{ns}:develop-running"})
        self._start(forge, "p0")
        assert forge.updates == []

    def test_repair_step_does_not_project(self, phased):
        phased()
        ns = _ns()
        forge = _LabelForge({f"{ns}:andon-decision", f"{ns}:develop-running"})
        self._start(forge, "repair")
        assert forge.updates == []

    def test_no_issue_number_skips_projection(self, phased):
        phased()
        forge = _mock_forge()
        with (
            patch("issuesmith.ops.dispatch.get_forge", return_value=forge),
            patch("issuesmith.ops.dispatch._try_python_step", return_value=0),
        ):
            assert dispatch.main(["p0"]) == 0
        forge.issue_get.assert_not_called()
        forge.issue_update.assert_not_called()


# ---------------------------------------------------------------------------
# Step forge guard: label writes inside a step
# ---------------------------------------------------------------------------

_STEP_MODULE = "issuesmith_test_label_writing_step"


def _install_label_writing_step(monkeypatch, step_forge) -> None:
    """A python step that writes a label through ``ghdag.forge.get_forge`` and comments."""
    import ghdag.forge as forge_mod

    monkeypatch.setattr(forge_mod, "get_forge", lambda *a, **k: step_forge)

    def run(ctx):
        from ghdag.forge import get_forge

        forge = get_forge()
        forge.issue_comment(42, "step comment")
        forge.issue_update(42, labels_add=["issuesmith:develop-done"])
        return StepResult(status="done", markers=["IMPL_DONE"])

    mod = types.ModuleType(_STEP_MODULE)
    mod.run = run
    monkeypatch.setitem(sys.modules, _STEP_MODULE, mod)
    monkeypatch.setattr(dispatch, "_STEP_MODULES", {"p0": _STEP_MODULE})


class TestStepLabelWriteGuard:
    def test_enforce_turns_label_write_into_broken_andon(self, phased, monkeypatch, capsys):
        phased(guard="enforce")
        ns = _ns()
        step_forge = _mock_forge()
        _install_label_writing_step(monkeypatch, step_forge)
        runner = _LabelForge({f"{ns}:develop-ready"})
        with patch("issuesmith.ops.dispatch.get_forge", return_value=runner):
            rc = dispatch.main(["p0", "issue_number=42", "workflow_name=issuesmith"])
        assert rc == 1
        step_forge.issue_comment.assert_called_once_with(42, "step comment")
        step_forge.issue_update.assert_not_called()
        # The runner (outside the guard) raised andon(broken) and projected it.
        assert runner.labels == {f"{ns}:andon-broken"}
        assert any("step p0 wrote labels" in c for c in runner.comments)
        assert "Traceback" not in capsys.readouterr().err

    def test_warn_passes_label_write_through(self, phased, monkeypatch, capsys):
        phased(guard="warn")
        ns = _ns()
        step_forge = _mock_forge()
        _install_label_writing_step(monkeypatch, step_forge)
        runner = _LabelForge({f"{ns}:develop-ready"})
        with patch("issuesmith.ops.dispatch.get_forge", return_value=runner):
            rc = dispatch.main(["p0", "issue_number=42", "workflow_name=issuesmith"])
        assert rc == 0
        step_forge.issue_update.assert_called_once_with(
            42, labels_add=["issuesmith:develop-done"]
        )
        assert "WARNING: step p0 wrote labels" in capsys.readouterr().err
        assert runner.labels == {f"{ns}:develop-running"}

    def test_get_forge_restored_after_the_step(self, phased, monkeypatch):
        import ghdag.forge as forge_mod

        phased(guard="enforce")
        step_forge = _mock_forge()
        _install_label_writing_step(monkeypatch, step_forge)
        factory = forge_mod.get_forge
        with patch("issuesmith.ops.dispatch.get_forge", return_value=_LabelForge(set())):
            dispatch.main(["p0", "issue_number=42", "workflow_name=issuesmith"])
        assert forge_mod.get_forge is factory


# ---------------------------------------------------------------------------
# Template / module resolution failure → andon(broken)
# ---------------------------------------------------------------------------

class TestResolutionFailureAndon:
    def test_template_not_found_raises_andon(self):
        dispatch_main = dispatch.main
        forge = _mock_forge()
        with (
            patch("issuesmith.ops.dispatch._try_python_step", return_value=None),
            patch(
                "issuesmith.ops.dispatch._run_bash_step",
                side_effect=FileNotFoundError("template not found"),
            ),
            patch("issuesmith.ops.dispatch.get_forge", return_value=forge),
            patch("issuesmith.ops.dispatch._raise_andon") as mock_raise,
        ):
            rc = dispatch_main(["broken-step", "issue_number=42", "workflow_name=issuesmith"])
        assert rc == 1
        mock_raise.assert_called_once()
        andon_arg = mock_raise.call_args[0][1]
        assert andon_arg.kind == "broken"

    def test_template_not_found_andon_id_includes_step(self):
        dispatch_main = dispatch.main
        forge = _mock_forge()
        with (
            patch("issuesmith.ops.dispatch._try_python_step", return_value=None),
            patch(
                "issuesmith.ops.dispatch._run_bash_step",
                side_effect=FileNotFoundError("template missing"),
            ),
            patch("issuesmith.ops.dispatch.get_forge", return_value=forge),
            patch("issuesmith.ops.dispatch._raise_andon") as mock_raise,
        ):
            dispatch_main(["my-step", "issue_number=99", "workflow_name=issuesmith"])
        andon_arg = mock_raise.call_args[0][1]
        assert "my-step" in andon_arg.id

    def test_undefined_template_variable_raises_andon(self):
        dispatch_main = dispatch.main
        forge = _mock_forge()
        with (
            patch("issuesmith.ops.dispatch._try_python_step", return_value=None),
            patch(
                "issuesmith.ops.dispatch._run_bash_step",
                side_effect=KeyError("undefined var"),
            ),
            patch("issuesmith.ops.dispatch.get_forge", return_value=forge),
            patch("issuesmith.ops.dispatch._raise_andon") as mock_raise,
        ):
            rc = dispatch_main(["broken-step", "issue_number=42", "workflow_name=issuesmith"])
        assert rc == 1
        mock_raise.assert_called_once()
        andon_arg = mock_raise.call_args[0][1]
        assert andon_arg.kind == "broken"
