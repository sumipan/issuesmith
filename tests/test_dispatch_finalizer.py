"""Tests for map_step_result label projection and broken-andon on template failure (#3509)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from issuesmith.steps.base import Andon, StepResult


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


# ---------------------------------------------------------------------------
# Marker → label projection
# ---------------------------------------------------------------------------

class TestMarkerLabelProjection:
    def test_merge_done_adds_merge_done_label(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = map_step_result(result, step_id="m2", context=_context())
        assert rc == 0
        forge.issue_update.assert_called_once()
        kwargs = forge.issue_update.call_args[1]
        assert "issuesmith:merge-done" in kwargs.get("labels_add", [])

    def test_merge_done_removes_merge_running(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            map_step_result(result, step_id="m2", context=_context())
        kwargs = forge.issue_update.call_args[1]
        assert "issuesmith:merge-running" in kwargs.get("labels_remove", [])

    def test_impl_done_adds_develop_done_label(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["IMPL_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            map_step_result(result, step_id="impl", context=_context())
        kwargs = forge.issue_update.call_args[1]
        assert "issuesmith:develop-done" in kwargs.get("labels_add", [])
        assert "issuesmith:develop-running" in kwargs.get("labels_remove", [])

    def test_report_done_adds_draft_done_label(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["REPORT_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            map_step_result(result, step_id="draft", context=_context())
        kwargs = forge.issue_update.call_args[1]
        assert "issuesmith:draft-done" in kwargs.get("labels_add", [])
        assert "issuesmith:draft-running" in kwargs.get("labels_remove", [])

    def test_unknown_marker_no_label_call(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["WORKTREE_READY"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = map_step_result(result, step_id="p0", context=_context())
        assert rc == 0
        forge.issue_update.assert_not_called()

    def test_label_projection_does_not_fail_on_forge_error(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        forge.issue_update.side_effect = RuntimeError("network error")
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = map_step_result(result, step_id="m2", context=_context())
        assert rc == 0  # warning printed but no crash

    def test_label_projection_skipped_when_no_issue_number(self):
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["MERGE_DONE"])
        ctx = _context(issue_number="")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = map_step_result(result, step_id="m2", context=ctx)
        assert rc == 0
        forge.issue_update.assert_not_called()

    def test_custom_namespace_used(self, monkeypatch):
        import issuesmith.config as cfg_module
        import dataclasses
        real_cfg = cfg_module.get_config()
        custom = dataclasses.replace(real_cfg, label_namespace="myns")
        monkeypatch.setattr(cfg_module, "_cached", custom)
        from issuesmith.ops.dispatch import map_step_result
        forge = _mock_forge()
        result = StepResult(status="done", markers=["MERGE_DONE"])
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            map_step_result(result, step_id="m2", context=_context())
        kwargs = forge.issue_update.call_args[1]
        assert "myns:merge-done" in kwargs.get("labels_add", [])
        cfg_module.reset_config_cache()


# ---------------------------------------------------------------------------
# Template / module resolution failure → andon(broken)
# ---------------------------------------------------------------------------

class TestResolutionFailureAndon:
    def test_template_not_found_raises_andon(self):
        from issuesmith.ops.dispatch import main as dispatch_main
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
        from issuesmith.ops.dispatch import main as dispatch_main
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
        from issuesmith.ops.dispatch import main as dispatch_main
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
