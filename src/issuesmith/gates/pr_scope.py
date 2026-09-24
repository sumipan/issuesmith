"""PR scope gate — wraps pr_scope.check_pr_diff_scope to return unified Verdict."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ghdag.workflow.gates import Violation

from issuesmith.gates import Verdict
from issuesmith.pr_scope import check_pr_diff_scope


def check_pr_scope(
    filenames: list[str],
    allow_paths: list[str],
    forbidden_patterns: list[str] | None = None,
    file_entries: Sequence[dict] | None = None,
) -> Verdict:
    """Check PR file scope and return Verdict."""
    violations = check_pr_diff_scope(filenames, allow_paths, forbidden_patterns, file_entries)
    if violations:
        return Verdict(passed=False, reasons=[v.message for v in violations])
    return Verdict(passed=True, reasons=[])


class PrScopeGate:
    """RequiresGate adapter: checks changed files (committed + uncommitted) against allow_paths.

    Returns one Violation per out-of-allow-paths file with rule_id='pr_scope.out_of_allow'.
    """

    def __init__(self, worktree_path: Path, allow_paths: list[str], base_branch: str) -> None:
        self._worktree_path = worktree_path
        self._allow_paths = allow_paths
        self._base_branch = base_branch

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        from issuesmith.gates.worktree import changed_files as _changed_files

        files = _changed_files(self._worktree_path, self._base_branch)
        violations = []
        for f in files:
            verdict = check_pr_scope([f], self._allow_paths)
            if not verdict.passed:
                violations.append(Violation(
                    rule_id="pr_scope.out_of_allow",
                    severity="fail",
                    message=f"{f}: outside allow_paths",
                    location=f,
                    auto_fixable=False,
                    fix_hint=f"widen:{f}",
                ))
        return violations


__all__ = ["check_pr_scope", "PrScopeGate"]
