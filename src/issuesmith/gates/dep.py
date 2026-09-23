"""Dependency gate — wraps dep_extractor.check_dependencies to return unified Verdict."""

from __future__ import annotations

from ghdag.forge import ForgePort
from ghdag.workflow.gates import Violation

from issuesmith.dep_extractor import check_dependencies, extract_dependencies
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


class DepsGate:
    """RequiresGate adapter: extracts deps from issue body and checks merge state."""

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        issue_numbers = extract_dependencies(body)
        if not issue_numbers:
            return []
        verdict = check_deps(issue_numbers)
        if verdict.passed:
            return []
        return [Violation(
            rule_id="deps.unmerged",
            severity="fail",
            message="; ".join(verdict.reasons),
            location=None,
            auto_fixable=False,
            fix_hint="Merge all dependency issues before proceeding",
        )]


__all__ = ["check_deps", "DepsGate"]
