"""V6 pre-creation dependency ref checks (#4911)."""

from __future__ import annotations

from issuesmith.language import EN
from issuesmith.milestone import check_v6_dependency_refs

DEPS_HEADING = EN.sections["dependencies"]
DEPS_TABLE_HEADER = EN.dependencies_table_header.strip()
NONE = EN.no_deps_word


def _body_with_deps(*, table_row: str) -> str:
    return (
        "```yaml\ntarget_repo: sumipan/nexus\nallow_paths:\n  - \"src/**\"\n```\n\n"
        f"## {DEPS_HEADING}\n\n"
        f"{DEPS_TABLE_HEADER}\n"
        "|---|---|---|\n"
        f"{table_row}\n"
    )


def test_v6_unresolved_dependency_table_row() -> None:
    body = _body_with_deps(
        table_row="| 1 | parent plan sub 2 (...) | prior |",
    )
    failures = check_v6_dependency_refs(body, "#4886")
    assert "unresolved plan ref #1 in dependency table" in failures


def test_v6_resolved_table_passes() -> None:
    body = _body_with_deps(
        table_row="| 1 | #4886 (title) | OPEN |",
    )
    assert check_v6_dependency_refs(body, "#4886") == []


def test_v6_dep_section_missing_issue_ref() -> None:
    body = _body_with_deps(
        table_row="| 1 | #4886 (title) | OPEN |",
    )
    body = body.replace("#4886", "#4887")
    failures = check_v6_dependency_refs(body, "#4886")
    assert failures == ["V6: dependencies section does not carry #4886"]


def test_v6_skipped_when_no_resolved_dep() -> None:
    body = "```yaml\ntarget_repo: sumipan/nexus\n```\n"
    assert check_v6_dependency_refs(body, NONE) == []
