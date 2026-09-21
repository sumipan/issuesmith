"""PR scope gate — wraps pr_scope.check_pr_diff_scope to return unified Verdict."""

from __future__ import annotations

from typing import Sequence

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


__all__ = ["check_pr_scope"]
