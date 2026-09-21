"""Dependency gate — wraps dep_extractor.check_dependencies to return unified Verdict."""

from __future__ import annotations

from ghdag.forge import ForgePort

from issuesmith.dep_extractor import check_dependencies
from issuesmith.gates import Verdict


def check_deps(
    issue_numbers: list[int],
    *,
    client: ForgePort | None = None,
) -> Verdict:
    """Verify that all dependency issues are satisfied and return Verdict."""
    result = check_dependencies(issue_numbers, client=client)
    if result.decision == "PASS":
        return Verdict(passed=True, reasons=[])
    reasons = [
        f"#{s.issue} state={s.state} has_merge_done={s.has_merge_done}"
        for s in result.blocking_deps
    ]
    return Verdict(passed=False, reasons=reasons)


__all__ = ["check_deps"]
