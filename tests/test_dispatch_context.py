"""Tests for _context_to_step execution_constraints extraction (#3445)."""

from __future__ import annotations

from issuesmith.ops.dispatch import _context_to_step


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
