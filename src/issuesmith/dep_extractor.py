"""Deterministic dependency extraction and verification for issuesmith B1."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field

from ghdag.forge import ForgePort, get_forge
from ghdag.markdown.body_editor import count_heading, get_section

from issuesmith.config import get_config

_MERGE_DONE_LABEL = "issuesmith:merge-done"
_REJECTED_LABEL = "issuesmith:rejected"
_SUB_DONE_LABEL = "issuesmith:sub-done"
_MILESTONE_LABEL = "scope:milestone"
_ISSUESMITH_LABEL_PREFIX = "issuesmith:"
_ANALYSIS_TITLE_PREFIX = "【障害分析】"
_ISSUE_NUM_RE = re.compile(r"#(\d+)")
_PARENT_ISSUE_RE = re.compile(r"^親イシュー:\s")
_CHILD_ISSUE_RE = re.compile(r"^子イシュー:\s")
_DEP_PREFIX_RE = re.compile(r"^依存:\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
UNPARSED_DEPENDENCY_SECTION = "unparsed_dependency_section"


@dataclass
class DepStatus:
    issue: int
    state: str
    has_merge_done: bool
    rescue_pr: int | None
    title: str
    is_exempt: bool

    @property
    def has_terminal_label(self) -> bool:
        return self.has_merge_done


@dataclass
class DepCheckResult:
    decision: str
    deps_found: list[int]
    blocking_deps: list[DepStatus]
    dep_statuses: list[DepStatus]
    unparsed_refs: list[int] = field(default_factory=list)
    reason: str = ""


def _iter_scannable_lines(body: str):
    """Yield lines outside fenced code blocks."""
    in_code_block = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        yield line


def _extract_issue_numbers(text: str) -> list[int]:
    return [int(m) for m in _ISSUE_NUM_RE.findall(text)]


def _is_excluded_line(line: str) -> bool:
    return bool(_PARENT_ISSUE_RE.match(line) or _CHILD_ISSUE_RE.match(line))


def _is_table_data_row(line: str) -> bool:
    """Return True for Markdown table data rows (not separator rows)."""
    stripped = line.strip()
    if not _TABLE_ROW_RE.match(stripped):
        return False
    if _TABLE_SEPARATOR_RE.match(stripped):
        return False
    return True


def _is_list_item(line: str) -> bool:
    """Return True for Markdown list items (``- ``, ``* ``, ``+ ``, ``1. ``, ``1) ``)."""
    return bool(_LIST_ITEM_RE.match(line))


def _section_dependencies(section: str) -> set[int]:
    """Issue numbers declared in the dependencies section.

    A declaration is a table data row or a list item. Prose lines are ignored
    here and reported by :func:`unparsed_dependency_refs`.
    """
    deps: set[int] = set()
    for line in _iter_scannable_lines(section):
        if _is_excluded_line(line):
            continue
        if not (_is_table_data_row(line) or _is_list_item(line)):
            continue
        deps.update(_extract_issue_numbers(line))
    return deps


def extract_dependencies(body: str) -> list[int]:
    """Extract dependency issue numbers from an Issue body deterministically."""
    deps: set[int] = set()

    deps_heading = get_config().sections["dependencies"]
    if count_heading(body, deps_heading) > 0:
        section = get_section(body, deps_heading) or ""
        deps.update(_section_dependencies(section))

    for line in _iter_scannable_lines(body):
        if _is_excluded_line(line):
            continue
        match = _DEP_PREFIX_RE.match(line)
        if match:
            deps.update(_extract_issue_numbers(match.group(1)))

    return sorted(deps)


def unparsed_dependency_refs(body: str) -> list[int]:
    """Issue refs in the dependencies section that no declaration line carries.

    A dependencies section that mentions ``#N`` only in prose would otherwise
    make the dependency gate pass as if nothing were declared (fail-open).
    Callers treat a non-empty result as BLOCK (``UNPARSED_DEPENDENCY_SECTION``).
    """
    deps_heading = get_config().sections["dependencies"]
    if count_heading(body, deps_heading) == 0:
        return []
    section = get_section(body, deps_heading) or ""
    declared = _section_dependencies(section)
    for line in _iter_scannable_lines(body):
        if _is_excluded_line(line):
            continue
        match = _DEP_PREFIX_RE.match(line)
        if match:
            declared.update(_extract_issue_numbers(match.group(1)))
    mentioned: set[int] = set()
    for line in _iter_scannable_lines(section):
        if _is_excluded_line(line):
            continue
        mentioned.update(_extract_issue_numbers(line))
    return sorted(mentioned - declared)


def _has_terminal_label(labels: list[dict], terminal_labels: tuple[str, ...]) -> bool:
    """Return True if any label in labels matches a terminal_labels entry."""
    label_names_set = {label.get("name") for label in labels}
    return bool(label_names_set & set(terminal_labels))


def _is_exempt(title: str, labels: list[dict]) -> bool:
    """Return True when a CLOSED dependency should be treated as satisfied.

    Exempt cases:
    1. Title starts with 【障害分析】 (analysis issues never get merge-done)
    2. Has issuesmith:rejected label
    3. Has no issuesmith:* labels at all (outside issuesmith lifecycle)
    4. Is a milestone that finished splitting (scope:milestone + issuesmith:sub-done).
       Milestones are closed by a human after all sub-issues merge and never get
       merge-done themselves (2026-09-05: #2821 was blocked by closed milestone #2820).
    """
    if title.startswith(_ANALYSIS_TITLE_PREFIX):
        return True
    label_names = [label.get("name") or "" for label in labels]
    if _REJECTED_LABEL in label_names:
        return True
    if _MILESTONE_LABEL in label_names and _SUB_DONE_LABEL in label_names:
        return True
    if not any(name.startswith(_ISSUESMITH_LABEL_PREFIX) for name in label_names):
        return True
    return False


def _find_rescue_pr(client: ForgePort, issue_number: int) -> int | None:
    timeline = client.issue_timeline(issue_number)
    pr_numbers: list[int] = []
    for event in timeline:
        if event.get("event") != "cross-referenced":
            continue
        source_issue = (event.get("source") or {}).get("issue") or {}
        if source_issue.get("pull_request") is not None:
            number = source_issue.get("number")
            if isinstance(number, int):
                pr_numbers.append(number)
    if not pr_numbers:
        return None

    pr_number = pr_numbers[-1]
    detail = client.pr_get(pr_number)
    if detail.get("merged") or detail.get("merged_at") or detail.get("mergedAt"):
        return pr_number
    if str(detail.get("state") or "").upper() == "MERGED":
        return pr_number
    return None


def get_dep_status(client: ForgePort, issue_number: int) -> DepStatus:
    """Return terminal-label status for a single dependency issue."""
    data = client.issue_get(issue_number, fields=["state", "labels", "title"])
    state = data.get("state", "UNKNOWN")
    labels = data.get("labels") or []
    title = data.get("title") or ""
    terminal_labels = get_config().terminal_labels
    has_merge_done = _has_terminal_label(labels, terminal_labels)
    is_exempt = _is_exempt(title, labels)

    rescue_pr = None
    if state == "CLOSED" and not has_merge_done and not is_exempt:
        rescue_pr = _find_rescue_pr(client, issue_number)

    return DepStatus(
        issue=issue_number,
        state=state,
        has_merge_done=has_merge_done,
        rescue_pr=rescue_pr,
        title=title,
        is_exempt=is_exempt,
    )


def is_satisfied(status: DepStatus) -> bool:
    # exempt は「CLOSED でも merge-done 不要」の緩和。OPEN のままでは未解消。
    if status.state == "CLOSED" and status.is_exempt:
        return True
    if status.state == "CLOSED" and status.has_merge_done:
        return True
    if status.state == "CLOSED" and status.rescue_pr is not None:
        return True
    return False


def _check_single_dependency(client: ForgePort, issue_number: int) -> DepStatus | None:
    """Return DepStatus when blocked, None when dependency is satisfied."""
    status = get_dep_status(client, issue_number)
    return None if is_satisfied(status) else status


def check_dependencies(
    issue_numbers: list[int],
    *,
    client: ForgePort | None = None,
    unparsed_refs: list[int] | None = None,
) -> DepCheckResult:
    """Verify that all dependency issues are merged.

    ``unparsed_refs`` (see :func:`unparsed_dependency_refs`) forces BLOCK with
    ``reason == UNPARSED_DEPENDENCY_SECTION`` before any forge call: a section
    that mentions issues without declaring them must not pass as "no deps".
    """
    deps_found = sorted(set(issue_numbers))
    unparsed = sorted(set(unparsed_refs or []))
    if unparsed:
        return DepCheckResult(
            decision="BLOCK",
            deps_found=deps_found,
            blocking_deps=[],
            dep_statuses=[],
            unparsed_refs=unparsed,
            reason=UNPARSED_DEPENDENCY_SECTION,
        )
    if not deps_found:
        # Empty list is vacuously PASS; avoid requiring auth / a client.
        return DepCheckResult(
            decision="PASS",
            deps_found=[],
            blocking_deps=[],
            dep_statuses=[],
        )
    gh = client or get_forge()
    dep_statuses = [get_dep_status(gh, number) for number in deps_found]
    blocking = [status for status in dep_statuses if not is_satisfied(status)]

    decision = "BLOCK" if blocking else "PASS"
    return DepCheckResult(
        decision=decision,
        deps_found=deps_found,
        blocking_deps=blocking,
        dep_statuses=dep_statuses,
    )


def _result_to_dict(result: DepCheckResult) -> dict:
    return {
        "decision": result.decision,
        "deps_found": result.deps_found,
        "blocking_deps": [asdict(dep) for dep in result.blocking_deps],
        "dep_statuses": [asdict(dep) for dep in result.dep_statuses],
        "unparsed_refs": result.unparsed_refs,
        "reason": result.reason,
    }


def check_issue(issue_number: int, *, client: ForgePort | None = None) -> DepCheckResult:
    """Fetch issue body, extract dependencies, and verify merge state."""
    gh = client or get_forge()
    body = gh.issue_get(issue_number, fields=["body"])["body"]
    deps = extract_dependencies(body)
    return check_dependencies(deps, client=gh, unparsed_refs=unparsed_dependency_refs(body))


def main() -> None:
    """CLI: python -m issuesmith.dep_extractor check <issue_number>"""
    if len(sys.argv) < 3 or sys.argv[1] != "check":
        print("Usage: python -m issuesmith.dep_extractor check <issue_number>", file=sys.stderr)
        sys.exit(1)

    issue_number = int(sys.argv[2])
    result = check_issue(issue_number)
    print(json.dumps(_result_to_dict(result)))


if __name__ == "__main__":
    main()
