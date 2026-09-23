"""Scope gate — wraps steps.scope_gate to return unified Verdict."""

from __future__ import annotations

from pathlib import Path

from ghdag.workflow.gates import Violation

from issuesmith.config import ScopeGateConfig, get_config
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


class ScopeGate:
    """RequiresGate adapter: checks allow_paths scope size against config threshold."""

    def __init__(self, worktree_path: Path, allow_paths: list[str]) -> None:
        self._worktree_path = worktree_path
        self._allow_paths = allow_paths

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        cfg = get_config().scope_gate
        verdict = check_scope(self._worktree_path, self._allow_paths, cfg)
        if verdict.passed:
            return []
        return [Violation(
            rule_id="scope.violation",
            severity="fail",
            message="; ".join(verdict.reasons),
            location=None,
            auto_fixable=False,
            fix_hint="Reduce allow_paths scope",
        )]


__all__ = ["check_scope", "ScopeGate"]
