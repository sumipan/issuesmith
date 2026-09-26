"""Dependency gate — wraps dep_extractor.check_dependencies to return unified Verdict."""

from __future__ import annotations

from typing import Any

from ghdag.forge import ForgePort
from ghdag.workflow.gates import Violation

from issuesmith.dep_extractor import (
    UNPARSED_DEPENDENCY_SECTION,
    check_dependencies,
    extract_dependencies,
    unparsed_dependency_refs,
)
from issuesmith.gate_rules.scope_coupling import (
    deletion_references_for_body,
    format_deletion_references,
)
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
    if result.reason == UNPARSED_DEPENDENCY_SECTION:
        refs = ", ".join(f"#{n}" for n in result.unparsed_refs)
        return Verdict(passed=False, reasons=[f"{UNPARSED_DEPENDENCY_SECTION}: {refs}"])
    reasons = [
        f"#{s.issue} state={s.state} has_merge_done={s.has_merge_done}"
        for s in result.blocking_deps
    ]
    return Verdict(passed=False, reasons=reasons)


class DepsGate:
    """RequiresGate adapter: extracts deps from issue body and checks merge state."""

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        unparsed = unparsed_dependency_refs(body)
        if unparsed:
            refs = ", ".join(f"#{n}" for n in unparsed)
            return [Violation(
                rule_id=f"deps.{UNPARSED_DEPENDENCY_SECTION}",
                severity="fail",
                message=f"dependencies section mentions {refs} without declaring them",
                location=None,
                auto_fixable=False,
                fix_hint="Declare each dependency as a table row or list item in the dependencies section",
            )]
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


def _recheck_marker(dep_issue_number: int) -> str:
    return f"<!-- issuesmith:scope-coupling-recheck dep=#{dep_issue_number} -->"


def dependents_of(dep_issue_number: int, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Issues (dicts with ``body``) whose dependencies section declares ``dep_issue_number``."""
    return [
        issue
        for issue in issues
        if dep_issue_number in extract_dependencies(str(issue.get("body") or ""))
    ]


def on_dep_merge_done(
    dep_issue_number: int,
    dependents: list[dict[str, Any]],
    *,
    client: ForgePort,
) -> list[int]:
    """Re-run the scope_coupling deletion check on dependents after a dependency merged (#3953).

    The dependency may have added new referrers of files a dependent deletes. Violations are
    reported as a comment on the dependent (once per dependency). Returns the Issue numbers
    that received a comment.
    """
    marker = _recheck_marker(dep_issue_number)
    commented: list[int] = []
    for issue in dependents:
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        refs = deletion_references_for_body(str(issue.get("body") or ""))
        if not refs:
            continue
        try:
            comments = client.get_issue_comments(number)
        except Exception:
            comments = []
        if isinstance(comments, list) and any(
            marker in str(c.get("body") or "") for c in comments if isinstance(c, dict)
        ):
            continue
        body = (
            "## Gate warning: uncovered references to deleted files "
            f"(re-check after dependency #{dep_issue_number} merged)\n\n"
            + format_deletion_references(refs, f"After #{dep_issue_number} merged, ")
            + f"\n\n{marker}"
        )
        client.issue_comment(number, body)
        commented.append(number)
    return commented


__all__ = ["check_deps", "DepsGate", "dependents_of", "on_dep_merge_done"]
