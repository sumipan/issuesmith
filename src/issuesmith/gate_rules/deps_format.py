"""Dependencies section format gate — unparsed #N refs without forge calls."""

from __future__ import annotations

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.dep_extractor import UNPARSED_DEPENDENCY_SECTION, unparsed_dependency_refs


class DepsFormatRules:
    """Fail when the dependencies section mentions issue numbers only in prose."""

    def __init__(self, self_issue: int | None = None) -> None:
        self.self_issue = self_issue

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        unparsed = unparsed_dependency_refs(body, self_issue=self.self_issue)
        if not unparsed:
            return []
        refs = ", ".join(f"#{n}" for n in unparsed)
        return [
            Violation(
                rule_id=f"deps.{UNPARSED_DEPENDENCY_SECTION}",
                severity="fail",
                message=f"dependencies section mentions {refs} without declaring them",
                location=None,
                auto_fixable=False,
                fix_hint=(
                    "Declare each dependency as a table row or list item in the "
                    "dependencies section. Put parent issues, reverse dependencies, "
                    "and background narrative outside that section."
                ),
            )
        ]


GATE_REGISTRY["deps_format"] = DepsFormatRules
