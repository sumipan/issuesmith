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


    def test_old_api_non_final_step_writes_nothing(self, phased):
        phased()
        forge = _mock_forge()
        result = StepResult(exit_code=0, pipeline_status="WORKTREE_READY")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            dispatch.map_step_result(result, step_id="p0", context=_context())
        forge.issue_update.assert_not_called()


    def test_default_config_m2_projects_merge_done(self):
        ns = _ns()
        forge = _LabelForge({f"{ns}:merge-running"})
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            dispatch.map_step_result(result, step_id="m2", context=_context())
        assert forge.labels == {f"{ns}:merge-done"}


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

