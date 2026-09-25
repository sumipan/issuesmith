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
    Files allowed only via ``derived_allow_paths`` (#3756) are checked by the derived test
    guard instead, so the allowance never applies without its guard.
    """

    def __init__(
        self,
        worktree_path: Path,
        allow_paths: list[str],
        base_branch: str,
        *,
        derived_allow_paths: list[str] | None = None,
    ) -> None:
        self._worktree_path = worktree_path
        self._allow_paths = allow_paths
        self._base_branch = base_branch
        self._derived_allow_paths = list(derived_allow_paths or [])

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        from issuesmith.config import get_config
        from issuesmith.gates import worktree as _worktree

        derived = (
            set(self._derived_allow_paths)
            if self._derived_allow_paths and get_config().derived_allow.enabled
            else set()
        )
        files = _worktree.changed_files(self._worktree_path, self._base_branch)
        violations = []
        guarded: list[str] = []
        for f in files:
            verdict = check_pr_scope([f], self._allow_paths)
            if verdict.passed:
                continue
            if f in derived:
                guarded.append(f)
                continue
            violations.append(Violation(
                rule_id="pr_scope.out_of_allow",
                severity="fail",
                message=f"{f}: outside allow_paths",
                location=f,
                auto_fixable=False,
                fix_hint=f"widen:{f}",
            ))
        if guarded:
            violations.extend(_worktree.check_derived_test_guard(
                self._worktree_path, self._base_branch, guarded
            ))
        return violations


__all__ = ["check_pr_scope", "PrScopeGate"]
