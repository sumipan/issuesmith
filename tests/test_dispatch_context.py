"""Tests for _context_to_step execution_constraints extraction (#3445).

Also covers step contract canonical location in issuesmith.contract (#4272).
"""

from __future__ import annotations

import pytest

from issuesmith.contract import Andon, StepContext, StepResult, Verdict
from issuesmith.ops.dispatch import _context_to_step


def test_contract_exports_step_types() -> None:
    """AC: StepContext / StepResult / Andon / Verdict importable from contract."""
    assert StepContext is not None
    assert StepResult is not None
    assert Andon is not None
    assert Verdict is not None


def test_step_result_compat_from_contract() -> None:
    """AC: contract StepResult preserves old exit_code/pipeline_status compat."""
    r = StepResult(exit_code=0, pipeline_status="M1_DONE")
    assert "M1_DONE" in r.markers
    assert r.status == "done"


def test_context_to_step_returns_contract_type() -> None:
    """AC: _context_to_step builds a contract.StepContext instance."""
    ctx = _context_to_step(
        {
            "issue_number": "1",
            "base_branch": "main",
            "handler_name": "sub",
            "is_cross_repo": "false",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "w",
            "m1_result_filename": "",
            "m1r_result_filename": "",
        }
    )
    assert isinstance(ctx, StepContext)


def test_context_to_step_with_execution_constraints() -> None:
    """AC-2: _context_to_step extracts execution_constraints from context dict."""
    ctx = _context_to_step(
        {
            "issue_number": "1",
            "base_branch": "main",
            "handler_name": "sub",
            "is_cross_repo": "false",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "w",
            "m1_result_filename": "",
            "m1r_result_filename": "",
            "execution_constraints": "X",
        }
    )
    assert ctx.execution_constraints == "X"


def test_context_to_step_without_execution_constraints() -> None:
    """AC-3: _context_to_step returns empty string when key is absent."""
    ctx = _context_to_step(
        {
            "issue_number": "1",
            "base_branch": "main",
            "handler_name": "sub",
            "is_cross_repo": "false",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "w",
            "m1_result_filename": "",
            "m1r_result_filename": "",
        }
    )
    assert ctx.execution_constraints == ""


def test_context_to_step_maps_host_companion_fields() -> None:
    ctx = _context_to_step(
        {
            "issue_number": "1",
            "base_branch": "main",
            "handler_name": "sub",
            "is_cross_repo": "true",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "w",
            "m1_result_filename": "",
            "m1r_result_filename": "",
            "has_host_changes": "true",
            "host_worktree_path": "/tmp/issue-1-host",
            "host_allow_paths": "- a",
        }
    )
    assert ctx.has_host_changes == "true"
    assert ctx.host_worktree_path == "/tmp/issue-1-host"
    assert ctx.host_allow_paths == "- a"


def test_context_to_step_ignores_legacy_companion_keys() -> None:
    """#5065: frozen orders passing the removed legacy keys still dispatch."""
    ctx = _context_to_step(
        {
            "issue_number": "1",
            "base_branch": "main",
            "handler_name": "sub",
            "is_cross_repo": "true",
            "target_clone_path": "",
            "source": "",
            "workflow_name": "w",
            "m1_result_filename": "",
            "m1r_result_filename": "",
            "diary_worktree_path": "x",
            "has_diary_changes": "true",
            "diary_allow_paths": "- p",
            "host_worktree_path": "y",
        }
    )
    assert ctx.host_worktree_path == "y"
    assert ctx.has_host_changes == ""
    assert ctx.host_allow_paths == ""


@pytest.mark.parametrize(
    "legacy_kw", ["diary_worktree_path", "has_diary_changes", "diary_allow_paths"]
)
def test_step_context_rejects_legacy_keyword(legacy_kw: str) -> None:
    with pytest.raises(TypeError):
        StepContext(
            issue_number="1",
            base_branch="main",
            handler_name="h",
            is_cross_repo="false",
            target_clone_path="",
            source="",
            workflow_name="w",
            m1_result_filename="",
            m1r_result_filename="",
            **{legacy_kw: "x"},
        )
