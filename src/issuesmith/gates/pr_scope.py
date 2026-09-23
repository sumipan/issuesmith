"""PR scope gate — wraps pr_scope.check_pr_diff_scope to return unified Verdict."""

from __future__ import annotations

import subprocess
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
    """RequiresGate adapter: checks PR diff files against allow_paths."""

    def __init__(self, worktree_path: Path, allow_paths: list[str], base_branch: str) -> None:
        self._worktree_path = worktree_path
        self._allow_paths = allow_paths
        self._base_branch = base_branch

    def _get_changed_files(self) -> list[str]:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"origin/{self._base_branch}...HEAD"],
            capture_output=True, text=True, check=False,
            cwd=str(self._worktree_path),
        )
        if proc.returncode != 0:
            return []
        return [f for f in proc.stdout.splitlines() if f.strip()]

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        files = self._get_changed_files()
        verdict = check_pr_scope(files, self._allow_paths)
        if verdict.passed:
            return []
        return [Violation(
            rule_id="pr_scope.violation",
            severity="fail",
            message="; ".join(verdict.reasons),
            location=None,
            auto_fixable=False,
            fix_hint="Only modify files listed in allow_paths",
        )]


__all__ = ["check_pr_scope", "PrScopeGate"]
