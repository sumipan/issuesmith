"""Deterministic dependency extraction and verification for issuesmith B1."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass

from ghdag.github_client import GitHubClient
from ghdag.markdown.body_editor import count_heading, get_section

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


@dataclass
class DepStatus:
    issue: int
    state: str
    has_merge_done: bool
    rescue_pr: int | None
    title: str
    is_exempt: bool


@dataclass
class DepCheckResult:
    decision: str
    deps_found: list[int]
    blocking_deps: list[DepStatus]
    dep_statuses: list[DepStatus]


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


def extract_dependencies(body: str) -> list[int]:
    """Extract dependency issue numbers from an Issue body deterministically."""
    deps: set[int] = set()

    if count_heading(body, "依存（先行）") > 0:
        section = get_section(body, "依存（先行）") or ""
        for line in section.splitlines():
            if _is_excluded_line(line):
                continue
            if not _is_table_data_row(line):
                continue
            deps.update(_extract_issue_numbers(line))

    for line in _iter_scannable_lines(body):
        if _is_excluded_line(line):
            continue
        match = _DEP_PREFIX_RE.match(line)
        if match:
            deps.update(_extract_issue_numbers(match.group(1)))

    return sorted(deps)


def _labels_include_merge_done(labels: list[dict]) -> bool:
    return any(label.get("name") == _MERGE_DONE_LABEL for label in labels)


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


def _find_rescue_pr(client: GitHubClient, issue_number: int) -> int | None:
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
    detail = client.api_request("GET", f"/repos/:owner/:repo/pulls/{pr_number}")
    if detail.get("merged") or detail.get("merged_at"):
        return pr_number
    return None


def _get_dep_status(client: GitHubClient, issue_number: int) -> DepStatus:
    """Return merge-check status for a single dependency issue."""
    data = client.issue_get(issue_number, fields=["state", "labels", "title"])
    state = data.get("state", "UNKNOWN")
    labels = data.get("labels") or []
    title = data.get("title") or ""
    has_merge_done = _labels_include_merge_done(labels)
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


def _is_satisfied(status: DepStatus) -> bool:
    # exempt は「CLOSED でも merge-done 不要」の緩和。OPEN のままでは未解消。
    if status.state == "CLOSED" and status.is_exempt:
        return True
    if status.state == "CLOSED" and status.has_merge_done:
        return True
    if status.state == "CLOSED" and status.rescue_pr is not None:
        return True
    return False


def _check_single_dependency(client: GitHubClient, issue_number: int) -> DepStatus | None:
    """Return DepStatus when blocked, None when dependency is satisfied."""
    status = _get_dep_status(client, issue_number)
    return None if _is_satisfied(status) else status


def check_dependencies(
    issue_numbers: list[int],
    *,
    client: GitHubClient | None = None,
) -> DepCheckResult:
    """Verify that all dependency issues are merged."""
    gh = client or GitHubClient()
    deps_found = sorted(set(issue_numbers))
    dep_statuses = [_get_dep_status(gh, number) for number in deps_found]
    blocking = [status for status in dep_statuses if not _is_satisfied(status)]

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
    }


def check_issue(issue_number: int, *, client: GitHubClient | None = None) -> DepCheckResult:
    """Fetch issue body, extract dependencies, and verify merge state."""
    gh = client or GitHubClient()
    body = gh.issue_get(issue_number, fields=["body"])["body"]
    deps = extract_dependencies(body)
    return check_dependencies(deps, client=gh)


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
