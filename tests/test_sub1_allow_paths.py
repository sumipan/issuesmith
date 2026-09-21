"""tests/test_sub1_allow_paths.py — SUB1 never inherits the parent's allow_paths (#3487 AC-4).

Mirrors nexus #3441: a parent Issue (target_repo: sumipan/nexus) wrote its
sub-design change table with the bold label the B1 gate requires
(``**changed_files**:``). SUB1 looked for an H2 heading, found nothing, and
copied the parent's ``scripts/** tools/** tests/**`` into every child —
including a sumipan/issuesmith child, where ``tests/**`` alone is 85 files.
``_allow_paths_for_row`` must read only the row's own change table (via
issuesmith.contract, the single parser the B1 gate also uses) and return []
— never the parent's allow_paths — when that table is unreadable.
"""

from __future__ import annotations

from issuesmith.steps.sub1_create import PlanRow, _allow_paths_for_row
from tests.legacy_text import CHANGE_TYPE, CHANGED_FILES, DESCRIPTION, FILE_PATH, MODIFY, REPOSITORY, SUB

_CHANGE_TABLE_HEADER = f"{REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION}"

_CHILD_PATHS = [
    "src/issuesmith/gate_rules/scope_breadth.py",
    "src/issuesmith/steps/p0_worktree.py",
    "src/issuesmith/steps/sub1_create.py",
]

_PARENT_ALLOW_PATHS = ["scripts/**", "tools/**", "tests/**"]


def _parent_body_bold_label_table() -> str:
    """A parent Issue (target_repo: sumipan/nexus) whose SUB1 change table uses
    the bold-label form (``**{CHANGED_FILES}**:``), not an H2 heading — the
    #3441 shape that made SUB1's old H2-only lookup silently fail over to the
    parent's allow_paths."""
    rows = "\n".join(
        f"| `sumipan/issuesmith` | `{p}` | {MODIFY} | narrow it |" for p in _CHILD_PATHS
    )
    allow_lines = "\n".join(f'  - "{p}"' for p in _PARENT_ALLOW_PATHS)
    return (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f"{allow_lines}\n"
        "```\n\n"
        "## Design\n\nParent design body.\n\n"
        f"#### {SUB}1: fix scope gate\n\n"
        "**Scope**: fix scope gate\n\n"
        f"**{CHANGED_FILES}**:\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        f"{rows}\n\n"
        f"#### {SUB}2: unrelated child, no table\n\n"
        "**Scope**: no table here at all\n\n"
    )


def test_row_gets_only_its_own_change_table_paths() -> None:
    """AC-4: SUB1 (target_repo sumipan/issuesmith) resolves to its 3 files only."""
    body = _parent_body_bold_label_table()
    row = PlanRow(row_num=1, title="fix scope gate", repo="sumipan/issuesmith", scope="x", dep_raw="")

    paths = _allow_paths_for_row(body, row)

    assert paths == _CHILD_PATHS
    for parent_path in _PARENT_ALLOW_PATHS:
        assert parent_path not in paths


def test_row_without_change_table_and_different_repo_returns_empty() -> None:
    """AC-4: no table + parent target_repo != row target_repo -> [] (caller must fail the row)."""
    body = _parent_body_bold_label_table()
    row = PlanRow(row_num=2, title="unrelated", repo="sumipan/mltgnt", scope="x", dep_raw="")

    paths = _allow_paths_for_row(body, row)

    assert paths == []
    for parent_path in _PARENT_ALLOW_PATHS:
        assert parent_path not in paths


def test_missing_sub_block_returns_empty() -> None:
    """A row number with no matching #### SUBn header at all also returns []."""
    body = _parent_body_bold_label_table()
    row = PlanRow(row_num=9, title="does not exist", repo="sumipan/issuesmith", scope="x", dep_raw="")

    assert _allow_paths_for_row(body, row) == []
