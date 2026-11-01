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

        cfg = get_config()
        derived_allow = cfg.derived_allow
        files = _worktree.changed_files(self._worktree_path, self._base_branch)
        violations: list[Violation] = []
        in_scope: list[str] = []
        out_of_scope: list[str] = []

        for f in files:
            file_violations = check_pr_diff_scope([f], self._allow_paths)
            if not file_violations:
                in_scope.append(f)
                continue
            v = file_violations[0]
            if v.rule_id == "pr_diff_scope.forbidden_path":
                violations.append(v)
                continue
            if v.rule_id == "pr_diff_scope.out_of_scope":
                out_of_scope.append(f)
                continue
            violations.append(v)

        derived: set[str] = set()
        if derived_allow.enabled:
            derived.update(self._derived_allow_paths)
            test_candidates = [
                f for f in out_of_scope
                if f.startswith("tests/") and f not in derived
            ]
            if test_candidates:
                derived.update(
                    _worktree.derive_static_test_allow_paths(
                        self._worktree_path,
                        self._base_branch,
                        test_candidates,
                        in_scope,
                        self._allow_paths,
                    )
                )
            if out_of_scope:
                derived.update(
                    _worktree.derive_static_ledger_allow_paths(
                        self._worktree_path,
                        self._base_branch,
                        out_of_scope,
                        self._allow_paths,
                        derived_allow.ledger_globs,
                    )
                )

        guarded: list[str] = []
        for f in out_of_scope:
            if f in derived:
                guarded.append(f)
                continue
            message = f"{f}: outside allow_paths"
            if derived_allow.enabled and f.startswith("tests/"):
                if not _worktree._exists_on_base(
                    self._worktree_path, self._base_branch, f
                ):
                    message = f"{f}: outside allow_paths (not on base)"
                else:
                    message = (
                        f"{f}: outside allow_paths (no reference to changed files)"
                    )
            violations.append(Violation(
                rule_id="pr_scope.out_of_allow",
                severity="fail",
                message=message,
                location=f,
                auto_fixable=False,
                fix_hint=f"widen:{f}",
            ))

        if guarded:
            violations.extend(_worktree.check_derived_test_guard(
                self._worktree_path, self._base_branch, guarded,
                ledger_globs=derived_allow.ledger_globs,
            ))
        return violations


__all__ = ["check_pr_scope", "PrScopeGate"]
