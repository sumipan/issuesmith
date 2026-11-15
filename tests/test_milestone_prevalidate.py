"""V6 pre-creation dependency ref checks (#4911)."""

from __future__ import annotations

from issuesmith.language import EN
from issuesmith.milestone import check_v6_dependency_refs

DEP_COL = EN.sub_plan_columns[4]
DEPS_HEADING = EN.sections["dependencies"]
DEPS_TABLE_HEADER = EN.dependencies_table_header.strip()
NONE = EN.no_deps_word


def _body_with_deps(*, dep_line: str, table_row: str) -> str:
    return (
        "```yaml\ntarget_repo: sumipan/nexus\nallow_paths:\n  - \"src/**\"\n```\n\n"
        f"{DEP_COL}: {dep_line}\n\n"
        f"## {DEPS_HEADING}\n\n"
        f"{DEPS_TABLE_HEADER}\n"
        "|---|---|---|\n"
        f"{table_row}\n"
    )


def test_v6_unresolved_dependency_table_row() -> None:
    body = _body_with_deps(
        dep_line="#4886",
        table_row="| 1 | parent plan sub 2 (...) | prior |",
    )
    failures = check_v6_dependency_refs(body, "#4886")
    assert "unresolved plan ref #1 in dependency table" in failures


def test_v6_resolved_table_and_dep_line_passes() -> None:
    body = _body_with_deps(
        dep_line="#4886",
        table_row="| 1 | #4886 (title) | OPEN |",
    )
    assert check_v6_dependency_refs(body, "#4886") == []


def test_v6_dep_line_missing_issue_ref() -> None:
    body = _body_with_deps(
        dep_line="sub 2",
        table_row="| 1 | #4886 (title) | OPEN |",
    )
    failures = check_v6_dependency_refs(body, "#4886")
    assert failures == ["V6: dependency line does not carry #4886"]


def test_v6_skipped_when_no_resolved_dep() -> None:
    body = "```yaml\ntarget_repo: sumipan/nexus\n```\n"
    assert check_v6_dependency_refs(body, NONE) == []


