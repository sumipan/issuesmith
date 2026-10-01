"""Tests for _context_to_step execution_constraints extraction (#3445).

Also covers step contract canonical location in issuesmith.contract (#4272).
"""

from __future__ import annotations

import warnings

import issuesmith.steps.base as legacy_base
from issuesmith.contract import Andon, StepContext, StepResult, Verdict
from issuesmith.ops.dispatch import _context_to_step


def test_contract_exports_step_types() -> None:
    """AC: StepContext / StepResult / Andon / Verdict importable from contract."""
    assert StepContext is not None
    assert StepResult is not None
    assert Andon is not None
    assert Verdict is not None


def test_steps_base_reexports_same_types() -> None:
    """AC: steps/base.py re-exports contract types without duplicating definitions."""
    assert legacy_base.StepContext is StepContext
    assert legacy_base.StepResult is StepResult
    assert legacy_base.Andon is Andon
    assert legacy_base.Verdict is Verdict


def test_steps_base_import_emits_deprecation_warning() -> None:
    """AC: legacy import path warns once per import."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        import importlib

        importlib.reload(__import__("issuesmith.steps.base", fromlist=["*"]))
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "issuesmith.contract" in str(w.message)
        for w in caught
    )


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
