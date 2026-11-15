"""Tests for _context_to_step execution_constraints extraction (#3445).

Also covers step contract canonical location in issuesmith.contract (#4272).
"""

from __future__ import annotations

from issuesmith.contract import Andon, StepContext, StepResult, Verdict


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


