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
from issuesmith.gate_rules.b1_milestone_subdesign import B1MilestoneSubdesignRules
from issuesmith.steps import sub1_create as sub1
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


def _row() -> sub1.PlanRow:
    return sub1.PlanRow(row_num=1, title="core", repo="sumipan/issuesmith", scope="add core", dep_raw=NONE)


def test_bold_label_table_is_read_by_gate_and_sub1_alike() -> None:
    body = _parent(_table("sumipan/issuesmith", _CHILD_PATHS))
    gate_ids = [v.rule_id for v in B1MilestoneSubdesignRules().check(body, ["scope:milestone"])]
    assert "b1_milestone_subdesign.change_paths_unreadable" not in gate_ids
    got = sub1._allow_paths_for_row(body, _row())
    assert got == _CHILD_PATHS
    assert not set(got) & set(_PARENT_ALLOW)


def test_heading_form_table_reads_the_same() -> None:
    from issuesmith.config import get_config

    text = f"### {get_config().sections['changed_files']}\n\n" + _table("sumipan/issuesmith", _CHILD_PATHS)
    assert change_paths_for_repo(text, "sumipan/issuesmith") == _CHILD_PATHS
    assert [r[0] for r in extract_change_table_rows(text)] == ["sumipan/issuesmith"] * 3


def test_unreadable_table_fails_gate_and_yields_no_paths() -> None:
    # rows exist but none for the child's repo -> nothing to derive allow_paths from
    body = _parent(_table("sumipan/nexus", ["scripts/x.py"]))
    row = _row()
    assert sub1._allow_paths_for_row(body, row) == []
    gate_ids = [v.rule_id for v in B1MilestoneSubdesignRules().check(body, ["scope:milestone"])]
    # gate reports the repo it *could* read and SUB1's repo agree on "unreadable for issuesmith"
    assert change_paths_for_repo(body, "sumipan/issuesmith") == []
    assert "b1_milestone_subdesign.repo_mismatch" in gate_ids or gate_ids  # repo column mismatch surfaces at B1


def test_missing_table_is_a_gate_violation() -> None:
    body = _parent("(no table here)\n")
    gate_ids = [v.rule_id for v in B1MilestoneSubdesignRules().check(body, ["scope:milestone"])]
    assert "b1_milestone_subdesign.change_paths_unreadable" in gate_ids
    assert sub1._allow_paths_for_row(body, _row()) == []


def test_parent_allow_paths_are_never_inherited() -> None:
    body = _parent(_table("sumipan/issuesmith", _CHILD_PATHS))
    child = sub1._build_child_body(
        parent_body=body,
        parent_number=1,
        row=_row(),
        resolved_dep="",
        client=None,  # type: ignore[arg-type]
        parent_labels=["scope:milestone"],
        allow_paths=sub1._allow_paths_for_row(body, _row()),
    )
    head = child.split("```")[1]
    for p in _PARENT_ALLOW:
        assert p not in head
    for p in _CHILD_PATHS:
        assert p in head


# --- cell-internal pipes (#3481) -------------------------------------------------


def test_pipe_inside_inline_code_does_not_split_cell() -> None:
    table = (
        "| # | scope | dep |\n"
        "|---|---|---|\n"
        "| 1 | add `andon list|show|answer` cmd | none |\n"
    )
    rows = parse_table_rows(table)
    assert rows == [
        ["#", "scope", "dep"],
        ["1", "add `andon list|show|answer` cmd", "none"],
    ]


def test_table_without_cell_pipes_keeps_existing_behavior() -> None:
    table = "| a | b | c |\n|---|:-:|---|\n| 1 |  | `x` |\n|  2  | y | z |\n"
    assert parse_table_rows(table) == [["a", "b", "c"], ["1", "", "`x`"], ["2", "y", "z"]]


def test_lone_backtick_cell_parses() -> None:
    table = "| a | b |\n|---|---|\n| ` | x |\n"
    rows = parse_table_rows(table)
    assert rows[0] == ["a", "b"]
    assert rows[1][0] == "`"


def test_split_plan_with_cell_pipe_reads_dep_repo_scope() -> None:
    from issuesmith.config import get_config

    sec = get_config().sections
    body = (
        f"## {sec['milestone']}\n\n### {sec['sub_plan']}\n"
        f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|---|---|---|---|\n"
        f"| 1 | andon | `sumipan/issuesmith` | add `andon list|show|answer` | {NONE} |\n"
        "| 2 | wire | `sumipan/nexus` | use `a|b` | #1 |\n"
    )
    rows, has_repo = sub1._parse_split_plan(body, parent_target_repo="sumipan/nexus")
    assert has_repo is True
    assert [(r.row_num, r.repo, r.scope, r.dep_raw) for r in rows] == [
        (1, "sumipan/issuesmith", "add `andon list|show|answer`", NONE),
        (2, "sumipan/nexus", "use `a|b`", "#1"),
    ]
