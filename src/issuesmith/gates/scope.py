"""Scope gate — wraps steps.scope_gate to return unified Verdict."""

from __future__ import annotations

from pathlib import Path

from issuesmith.config import ScopeGateConfig
from issuesmith.gates import Verdict
from issuesmith.steps.scope_gate import evaluate, measure_scope


def check_scope(
    worktree_root: Path,
    allow_paths: list[str],
    config: ScopeGateConfig,
    override: ScopeGateConfig | None = None,
) -> Verdict:
    """Measure allow_paths scope and return Verdict."""
    scope_measure = measure_scope(worktree_root, allow_paths)
    scope_verdict = evaluate(scope_measure, config, override=override)
    if scope_verdict.exceeded:
        return Verdict(passed=False, reasons=[scope_verdict.reason])
    return Verdict(passed=True, reasons=[])


__all__ = ["check_scope"]
