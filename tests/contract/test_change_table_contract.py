"""R2 — the change table read by the B1 gate and by SUB1 is one contract (#3487).

The parent of #3483 (nexus #3441) wrote the sub-design change table with the
bold label the B1 gate *requires*, but SUB1 looked for an H2 heading, found
nothing, and silently copied the parent's ``tests/**`` into an issuesmith
child. Both sides now call ``issuesmith.contract.change_paths_for_repo``; this
module feeds the same fixture to both and asserts they agree.
"""

from __future__ import annotations

from issuesmith.contract import (
    change_paths_for_repo,
    extract_change_table_rows,
    parse_table_rows,
)
from issuesmith.gate_rules.milestone_subdesign_rules import MilestoneSubdesignRules
from issuesmith.milestone import PlanRow, allow_paths_for_row
from tests.legacy_text import (
    ADD,
    CHANGE_TYPE,
    CONTENT,
    DEPENDENCY,
    DESCRIPTION,
    FILE_PATH,
    MODIFY,
    NONE,
    REPOSITORY,
    SUB,
    TARGET_REPOSITORY,
    TITLE,
)

_HEADER = f"| {REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |\n|---|---|---|---|\n"
_CHILD_PATHS = ["src/issuesmith/andon.py", "src/issuesmith/__main__.py", "tests/test_andon.py"]
_PARENT_ALLOW = ["scripts/**", "tools/**", "tests/**"]


def _table(repo: str, paths: list[str]) -> str:
    rows = "".join(
        f"| `{repo}` | `{p}` | {ADD if i else MODIFY} | x |\n" for i, p in enumerate(paths)
    )
    return _HEADER + rows


def _parent(sub_block_table: str, *, child_repo: str = "sumipan/issuesmith") -> str:
    """Parent body shaped like nexus #3441: bold-label change table inside #### Sub1.

    Section names come from the configured sections (conftest sets ASCII names),
    exactly as B1 templates render them.
    """
    from issuesmith.config import get_config

    sec = get_config().sections
    subs = get_config().sub_design_subsections  # (Scope, Design Policy, Changed Files, AC)
    allow = "\n".join(f'  - "{p}"' for p in _PARENT_ALLOW)
    return (
        "```yaml\ntarget_repo: sumipan/nexus\nbase_branch: main\nallow_paths:\n"
        f"{allow}\n```\n\n"
        f"## {sec['design']}\n\nparent design\n\n"
        f"#### {SUB}1: core\n\n"
        f"**{subs[0]}**: add core\n\n"
        f"**{subs[1]}**: typed signal\n\n"
        f"**{sec['changed_files']}**:\n{sub_block_table}\n"
        f"**{subs[3]}**:\n- [ ] a\n- [ ] b\n- [ ] c\n\n"
        f"## {sec['acceptance_criteria']}\n\n- [x] ok\n\n"
        f"## {sec['milestone']}\n\n### {sec['sub_plan']}\n"
        f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|---|---|---|---|\n"
        f"| 1 | core | `{child_repo}` | add core | {NONE} |\n"
    )


def _row() -> PlanRow:
    return PlanRow(row_num=1, title="core", repo="sumipan/issuesmith", scope="add core", dep_raw=NONE)


def test_bold_label_table_is_read_by_gate_and_sub1_alike() -> None:
    body = _parent(_table("sumipan/issuesmith", _CHILD_PATHS))
    gate_ids = [v.rule_id for v in MilestoneSubdesignRules().check(body, ["scope:milestone"])]
    assert "milestone_subdesign.change_paths_unreadable" not in gate_ids
    got = allow_paths_for_row(body, _row())
    assert got == _CHILD_PATHS
    assert not set(got) & set(_PARENT_ALLOW)


def test_heading_form_table_reads_the_same() -> None:
    from issuesmith.config import get_config

    text = f"### {get_config().sections['changed_files']}\n\n" + _table("sumipan/issuesmith", _CHILD_PATHS)
    assert change_paths_for_repo(text, "sumipan/issuesmith") == _CHILD_PATHS
    assert [r[0] for r in extract_change_table_rows(text)] == ["sumipan/issuesmith"] * 3


def test_missing_table_is_a_gate_violation() -> None:
    body = _parent("(no table here)\n")
    gate_ids = [v.rule_id for v in MilestoneSubdesignRules().check(body, ["scope:milestone"])]
    assert "milestone_subdesign.change_paths_unreadable" in gate_ids
    assert allow_paths_for_row(body, _row()) == []


# --- cell-internal pipes (#3481) -------------------------------------------------


def test_table_without_cell_pipes_keeps_existing_behavior() -> None:
    table = "| a | b | c |\n|---|:-:|---|\n| 1 |  | `x` |\n|  2  | y | z |\n"
    assert parse_table_rows(table) == [["a", "b", "c"], ["1", "", "`x`"], ["2", "y", "z"]]


def test_lone_backtick_cell_parses() -> None:
    table = "| a | b |\n|---|---|\n| ` | x |\n"
    rows = parse_table_rows(table)
    assert rows[0] == ["a", "b"]
    assert rows[1][0] == "`"


def test_repo_root_file_is_returned_from_four_column_table() -> None:
    # #4851: root-level files (no "/") must not be dropped by change_paths_for_repo
    text = _HEADER + f"| sumipan/issuesmith | issuesmith.yaml | {MODIFY} | x |\n"
    assert change_paths_for_repo(text, "sumipan/issuesmith") == ["issuesmith.yaml"]


def test_root_only_sub_block_is_readable_by_gate() -> None:
    body = _parent(_table("sumipan/issuesmith", ["issuesmith.yaml", "pyproject.toml"]))
    gate_ids = [v.rule_id for v in MilestoneSubdesignRules().check(body, ["scope:milestone"])]
    assert "milestone_subdesign.change_paths_unreadable" not in gate_ids
    assert allow_paths_for_row(body, _row()) == ["issuesmith.yaml", "pyproject.toml"]
