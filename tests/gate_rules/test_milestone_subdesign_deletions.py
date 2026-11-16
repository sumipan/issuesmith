"""b1_milestone_subdesign: deletion references, referrer dependencies and behavior tests in siblings."""

from __future__ import annotations

import pytest

import issuesmith.gate_rules.milestone_subdesign_rules  # noqa: F401
from issuesmith.config import get_config
from tests.gate_rules.test_milestone_subdesign import (
    _TABLE_HEADER,
    MILESTONE_LABELS,
    _check,
    _milestone_body,
    _rule_ids,
    _sub_block,
    _valid_body,
    _with_test_sub_dependency,
)
from tests.legacy_text import SUB

# ---------------------------------------------------------------------------
# #4518 — milestone splits that P1 cannot implement
# ---------------------------------------------------------------------------

_ORPHAN_RULE = "milestone_subdesign.deletion_reference_orphan"
_SIBLING_TEST_RULE = "milestone_subdesign.behavior_test_in_sibling"
_CONTRADICTION_RULE = "milestone_subdesign.sub_ac_contradiction"

_STANDALONE_IMPORT_ERROR = "This sub alone raises ImportError in test collection."
_PARENT_FULL_PASS = "The full test pass is checked by the parent."
_EXISTING_TESTS_PASS = "All existing tests pass"


def _git_repo(root, files: dict[str, str]):
    import subprocess

    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "add", "-A"],
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
    ):
        subprocess.run(cmd, cwd=root, check=True)
    return root


@pytest.fixture
def deletion_repo(tmp_path, monkeypatch):
    """Clone with ``old.py`` imported by ``user.py``; resolve_scope_root points at it."""
    root = _git_repo(tmp_path / "repo", {
        "src/pkg/old.py": "VALUE = 1\n",
        "src/pkg/user.py": "from pkg.old import VALUE\n",
    })
    monkeypatch.setattr(
        "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
        lambda metadata, cfg: root,
    )
    return root


def test_deletion_reference_orphan_when_no_sub_covers_referrer(deletion_repo):
    body = _milestone_body([
        ("drop old", [("src/pkg/old.py", "Delete")]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    orphans = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _ORPHAN_RULE]
    assert len(orphans) == 1
    assert "src/pkg/old.py" in orphans[0].message
    assert "src/pkg/user.py" in orphans[0].message
    assert orphans[0].fix_hint


def test_deletion_reference_orphan_passes_when_sibling_covers_referrer(deletion_repo):
    body = _milestone_body([
        ("drop old", [("src/pkg/old.py", "Delete")]),
        ("follow", [("src/pkg/user.py", "Modify")]),
    ])
    assert _ORPHAN_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_deletion_reference_orphan_skipped_without_clone(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
        lambda metadata, cfg: None,
    )
    monkeypatch.setattr(
        "issuesmith.gate_rules.scope_coupling.deletion_references_for_body",
        lambda body: calls.append(body) or {"src/pkg/old.py": ["src/pkg/user.py"]},
    )
    body = _milestone_body([
        ("drop old", [("src/pkg/old.py", "Delete")]),
        ("docs", [("docs/a.md", "Modify")]),
    ])
    assert _ORPHAN_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))
    assert calls == []





def test_behavior_test_in_sibling_ignores_partial_stem_match():
    body = _milestone_body([
        ("impl", [("src/pkg/a.py", "Modify")]),
        ("tests", [("tests/test_data.py", "Modify")]),
    ])
    assert _SIBLING_TEST_RULE not in _rule_ids(_check(body, MILESTONE_LABELS))


def _contradiction_body(policy: str, ac_items: list[str]) -> str:
    block = _sub_block(1, "drop", "tools/foo/a.py", ac_items=ac_items).replace(
        "**Design Policy**: Sub1 c306E_Design Policy", f"**Design Policy**: {policy}"
    )
    return _valid_body().replace(_sub_block(1, "foo", "tools/foo/a.py"), block)


_PLAIN_AC = ["Sub1 alpha", "Sub1 beta", "Sub1 gamma"]


def test_sub_ac_contradiction_detected():
    body = _contradiction_body(
        f"{_STANDALONE_IMPORT_ERROR} {_PARENT_FULL_PASS}",
        [*_PLAIN_AC, _EXISTING_TESTS_PASS],
    )
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _CONTRADICTION_RULE]
    assert len(hits) == 1
    assert hits[0].location == f"#### {SUB}1"
    assert hits[0].fix_hint


def test_sub_ac_contradiction_needs_both_conditions():
    only_a = _contradiction_body(_STANDALONE_IMPORT_ERROR, _PLAIN_AC)
    only_b = _contradiction_body("plain policy", [*_PLAIN_AC, _EXISTING_TESTS_PASS])
    assert _CONTRADICTION_RULE not in _rule_ids(_check(only_a, MILESTONE_LABELS))
    assert _CONTRADICTION_RULE not in _rule_ids(_check(only_b, MILESTONE_LABELS))



# ---------------------------------------------------------------------------
# #4912 — glob / deletion sibling test matching (#4793)
# ---------------------------------------------------------------------------


def _milestone_body_rows(
    subs: list[tuple[str, list[tuple[str, str, str]]]],
    *,
    allow_paths: tuple[str, ...] = ("tools/**", "tests/**", "src/**"),
) -> str:
    """Like ``_milestone_body`` but each row is ``(path, change_type, content)``."""
    blocks: list[str] = []
    plan_rows: list[str] = []
    for num, (policy, rows) in enumerate(subs, start=1):
        table = "\n".join(
            f"| `sumipan/nexus` | `{path}` | {change_type} | {content} |"
            for path, change_type, content in rows
        )
        blocks.append(f"""\
#### {SUB}{num}: sub{num}

**Scope**: scope {num}
**Design Policy**: {policy}
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
{table}

**Acceptance Criteria**:
- [ ] Sub{num} alpha
- [ ] Sub{num} beta
- [ ] Sub{num} gamma
""")
        plan_rows.append(f"| {num} | sub{num} | scope{num} | None |")
    allow = "\n".join(f"  - {p}" for p in allow_paths)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
{allow}
```

## Design

{chr(10).join(blocks)}
## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
{chr(10).join(plan_rows)}
"""


def _sibling_hits(body: str):
    return [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _SIBLING_TEST_RULE]




def test_behavior_test_in_sibling_glob_dependency_exemption_without_deletion():
    body = _with_test_sub_dependency(
        _milestone_body_rows([
            ("impl", [("tools/corklab/config.py", "Modify", "refactor only")]),
            ("tests", [("tests/tools/corklab/test_*.py", "Modify", "update tests")]),
        ]),
        "#1",
    )
    assert _sibling_hits(body) == []


def test_behavior_test_in_sibling_src_layout_glob_mirror():
    body = _milestone_body_rows([
        ("impl", [("src/pkg/gate_rules/foo.py", "Modify", "change")]),
        ("tests", [("tests/gate_rules/test_*.py", "Modify", "update")]),
    ])
    assert len(_sibling_hits(body)) == 1


def test_behavior_test_in_sibling_deletion_prefix_despite_dependency():
    delete_word = get_config().language.delete_words[0]
    body = _with_test_sub_dependency(
        _milestone_body_rows([
            ("impl", [("tools/corklab/labels.py", f"Modify {delete_word}", "drop labels")]),
            ("tests", [("tests/tools/corklab/test_steps_verify.py", "Add", "new test")]),
        ]),
        "#1",
    )
    hits = _sibling_hits(body)
    assert len(hits) == 1
    assert "(deletion)" in hits[0].message


def test_behavior_test_in_sibling_glob_no_false_positive_other_dir():
    body = _milestone_body_rows([
        ("impl", [("src/x/a.py", "Modify", "change")]),
        ("tests", [("tests/y/test_*.py", "Modify", "update")]),
    ])
    assert _sibling_hits(body) == []


def test_behavior_test_in_sibling_deletion_no_false_positive_other_dir():
    delete_word = get_config().language.delete_words[0]
    body = _milestone_body_rows([
        ("impl", [("tools/foo/a.py", f"Modify {delete_word}", "drop")]),
        ("tests", [("tests/tools/bar/test_b.py", "Add", "test")]),
    ])
    assert _sibling_hits(body) == []


# ---------------------------------------------------------------------------
# #5750 — deletion AC consistency and sibling referrer dependencies
# ---------------------------------------------------------------------------

_DELETED_IN_EXIST = "milestone_subdesign.deleted_path_in_paths_must_exist"
_REFERRER_DEP = "milestone_subdesign.deletion_referrer_in_sibling_without_dependency"


def _sub_with_structured_ac(
    num: int,
    title: str,
    rows: list[tuple[str, str, str]],
    *,
    paths_must_exist: list[str] | None = None,
    paths_must_not_exist: list[str] | None = None,
) -> str:
    table = "\n".join(
        f"| `sumipan/nexus` | `{path}` | {change_type} | {desc} |"
        for path, change_type, desc in rows
    )
    exist = paths_must_exist if paths_must_exist is not None else []
    not_exist = paths_must_not_exist if paths_must_not_exist is not None else []
    exist_yaml = (
        "paths_must_exist: []"
        if not exist
        else "paths_must_exist:\n" + "\n".join(f"  - {p}" for p in exist)
    )
    not_yaml = (
        "paths_must_not_exist: []"
        if not not_exist
        else "paths_must_not_exist:\n" + "\n".join(f"  - {p}" for p in not_exist)
    )
    return f"""\
#### {SUB}{num}: {title}

**Scope**: scope {num}
**Design Policy**: policy {num}
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
{table}

**Acceptance Criteria**:
```yaml
{exist_yaml}
{not_yaml}
```
- [ ] alpha {num}
- [ ] beta {num}
- [ ] gamma {num}
"""


def _milestone_with_subs(
    subs: list[str],
    *,
    plan_deps: list[str] | None = None,
    parent_prefix: str = "",
    parent_ac: str = "",
    allow_paths: tuple[str, ...] = ("docs/**",),
) -> str:
    cfg = get_config()
    plan_deps = plan_deps or [cfg.language.no_deps_word] * len(subs)
    plan_rows = "\n".join(
        f"| {i + 1} | sub{i + 1} | sumipan/nexus | scope{i + 1} | {plan_deps[i]} |"
        for i in range(len(subs))
    )
    parent_ac_block = parent_ac or "```yaml\npaths_must_exist: []\npaths_must_not_exist: []\n```"
    allow = "\n".join(f"  - {p}" for p in allow_paths)
    return f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
{allow}
```

## {cfg.sections['design']}

{parent_prefix}
{chr(10).join(subs)}

## {cfg.sections['milestone']}

### {cfg.sections['sub_plan']}
{cfg.language.sub_plan_header}
|---|---|---|---|---|
{plan_rows}

## {cfg.sections['acceptance_criteria']}

{parent_ac_block}
"""





def test_deleted_path_in_paths_must_exist_parent_table_only():
    parent_table = f"""\
**Changed Files**:
| {_TABLE_HEADER} |
|---|---|---|---|
| `sumipan/nexus` | `tools/parent/old.py` | Delete | drop |
"""
    parent_ac = """```yaml
paths_must_exist:
  - tools/parent/old.py
paths_must_not_exist: []
```"""
    body = _milestone_with_subs(
        [_sub_with_structured_ac(1, "sub", [("src/x.py", "Add", "new")])],
        parent_prefix=parent_table,
        parent_ac=parent_ac,
    )
    hits = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _DELETED_IN_EXIST]
    assert len(hits) == 1
    assert "tools/parent/old.py" in hits[0].message
    assert hits[0].location == "## Acceptance Criteria"


def test_deleted_path_ac_skipped_when_yaml_invalid():
    block = _sub_with_structured_ac(
        1,
        "drop",
        [("tools/foo/old.py", "Delete", "remove")],
        paths_must_exist=["tools/foo/old.py"],
    ).replace(
        "paths_must_exist:",
        "paths_must_exist: not-a-list",
        1,
    )
    body = _milestone_with_subs([block])
    assert _DELETED_IN_EXIST not in _rule_ids(_check(body, MILESTONE_LABELS))



def test_deletion_referrer_dependency_passes_with_hash_dep(deletion_repo):
    body = _milestone_with_subs(
        [
            _sub_with_structured_ac(
                1,
                "drop",
                [("src/pkg/old.py", "Delete", "remove")],
                paths_must_not_exist=["src/pkg/old.py"],
            ),
            _sub_with_structured_ac(
                2,
                "follow",
                [("src/pkg/user.py", "Modify", "update import")],
            ),
        ],
        plan_deps=["#2", get_config().language.no_deps_word],
    )
    assert _REFERRER_DEP not in _rule_ids(_check(body, MILESTONE_LABELS))


def test_deletion_referrer_dependency_skipped_when_referrer_in_same_sub(deletion_repo):
    body = _milestone_with_subs([
        _sub_with_structured_ac(
            1,
            "drop",
            [
                ("src/pkg/old.py", "Delete", "remove"),
                ("src/pkg/user.py", "Modify", "update import"),
            ],
            paths_must_not_exist=["src/pkg/old.py"],
        ),
    ])
    assert _REFERRER_DEP not in _rule_ids(_check(body, MILESTONE_LABELS))


