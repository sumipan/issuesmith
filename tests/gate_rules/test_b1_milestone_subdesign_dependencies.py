"""b1_milestone_subdesign: sub dependency inference, ordering and cycles."""

from __future__ import annotations

import pytest

import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: F401
from tests.gate_rules.test_b1_milestone_subdesign import (
    _REPO,
    MILESTONE_LABELS,
    _check,
    _cycle_subs,
    _dep_body,
    _dep_sub,
    _milestone_body,
    _plan_row,
    _projection_subs,
    _use_pack,
    _with_test_sub_dependency,
)
from tests.legacy_text import CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY, SUB

# --- #4825: sibling new-file dependencies -------------------------------------------

_DEP_MISSING = "b1_milestone_subdesign.sibling_new_file_unreferenced_dependency"
_DEP_CYCLE = "b1_milestone_subdesign.dependency_cycle"


def _dep_ids(body: str) -> list[str]:
    return [v.rule_id for v in _check(body, MILESTONE_LABELS) if v.rule_id.startswith(
        (_DEP_MISSING, _DEP_CYCLE)
    )]


def test_infer_sub_dependencies_projection_fixture():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        extract_sub_blocks,
        infer_sub_dependencies,
    )

    body = _dep_body(_projection_subs(), ["none", "none"])
    assert infer_sub_dependencies(extract_sub_blocks(body)) == {1: set(), 2: {1}}
    violations = [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _DEP_MISSING]
    assert len(violations) == 1
    (v,) = violations
    assert v.severity == "fail" and v.auto_fixable is True
    assert "Sub 2" in v.message and "Sub 1" in v.message
    assert "src/issuesmith/projection.py" in v.message


@pytest.mark.parametrize(
    "where",
    ["policy", "content", "ac"],
)
def test_infer_reads_policy_content_and_ac(where):
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        extract_sub_blocks,
        infer_sub_dependencies,
    )

    ref = "use projection here"
    sub2 = _dep_sub(
        2,
        [("src/issuesmith/ops/dispatch.py", "modify", ref if where == "content" else "route")],
        policy=ref if where == "policy" else "",
        ac_items=[ref, "b item", "c item"] if where == "ac" else None,
    )
    body = _dep_body([_projection_subs()[0], sub2], ["none", "none"])
    assert infer_sub_dependencies(extract_sub_blocks(body)) == {1: set(), 2: {1}}


def test_infer_is_deterministic():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        extract_sub_blocks,
        infer_sub_dependencies,
    )

    blocks = extract_sub_blocks(_dep_body(_cycle_subs(), ["none", "none"]))
    assert infer_sub_dependencies(blocks) == infer_sub_dependencies(blocks) == {1: {2}, 2: {1}}


@pytest.mark.parametrize(
    "case",
    ["self", "other_repo", "stem_partial", "url", "fence_lang", "path_cell", "duplicate_path",
     "declared"],
)
def test_no_false_missing_dependency(case):
    sub1 = _projection_subs()[0]
    deps = ["none", "none"]
    if case == "self":
        sub1 = _dep_sub(1, [("src/issuesmith/projection.py", "add", "see projection.py")],
                        policy="own `projection.py`")
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route")])
    elif case == "other_repo":
        sub2 = _dep_sub(2, [("src/x/dispatch.py", "modify", "use projection.py")],
                        policy="read `projection.py`", repo="sumipan/nexus")
    elif case == "stem_partial":
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "project projections")],
                        policy="projection_store and myprojection")
    elif case == "url":
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route")],
                        policy="see https://example.com/src/issuesmith/projection.py")
    elif case == "fence_lang":
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "route")],
                        policy="snippet:\n```projection\nx = 1\n```")
    elif case == "path_cell":
        sub2 = _dep_sub(2, [("src/issuesmith/other/projection.py", "modify", "route")])
    elif case == "duplicate_path":
        sub2 = _dep_sub(2, [("src/issuesmith/projection.py", "modify", "tweak projection")])
    else:
        sub2 = _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "use projection")])
        deps = ["none", "#1"]
    body = _dep_body([sub1, sub2], deps)
    assert _DEP_MISSING not in _dep_ids(body)


def test_declared_dependency_order_and_format_are_normalized():
    blocks = [*_projection_subs(), _dep_sub(3, [("src/issuesmith/z.py", "modify", "z")])]
    assert _DEP_MISSING not in _dep_ids(_dep_body(blocks, ["none", "3, #1", "none"]))


def test_dependency_cycle_is_reported():
    body = _dep_body(_cycle_subs(), ["none", "none"])
    violations = [
        v for v in _check(body, MILESTONE_LABELS) if v.rule_id in (_DEP_MISSING, _DEP_CYCLE)
    ]
    assert [v.rule_id for v in violations] == [_DEP_CYCLE]
    (cycle,) = violations
    assert cycle.severity == "fail" and cycle.auto_fixable is False
    assert "1 -> 2 -> 1" in cycle.message
    assert "merge" in cycle.fix_hint and "stub" in cycle.fix_hint


def test_declared_self_dependency_is_a_cycle():
    body = _dep_body(_projection_subs(), ["#1", "#1"])
    assert _dep_ids(body) == [_DEP_CYCLE]


def test_declared_edge_closing_cycle_is_reported():
    body = _dep_body(_projection_subs(), ["#2", "none"])
    assert _dep_ids(body) == [_DEP_CYCLE]


def test_apply_inferred_dependency_fixes_replaces_none():
    from issuesmith.gate_rules.b1_milestone_subdesign import apply_inferred_dependency_fixes

    body = _dep_body(_projection_subs(), ["none", "none"])
    fixed, applied = apply_inferred_dependency_fixes(body)
    assert applied == [_DEP_MISSING]
    assert _plan_row(fixed, 2) == f"| 2 | part 2 | {_REPO} | c | #1 |"
    assert _plan_row(fixed, 1) == _plan_row(body, 1)
    # Only the depends-on cell changed.
    assert fixed.replace("| c | #1 |", "| c | none |") == body
    assert _DEP_MISSING not in _dep_ids(fixed)
    assert apply_inferred_dependency_fixes(fixed) == (fixed, [])


def test_apply_inferred_dependency_fixes_merges_existing_and_empty():
    from issuesmith.gate_rules.b1_milestone_subdesign import apply_inferred_dependency_fixes

    blocks = [*_projection_subs(), _dep_sub(3, [("src/issuesmith/z.py", "modify", "z")])]
    fixed, _ = apply_inferred_dependency_fixes(_dep_body(blocks, ["none", "#3", "none"]))
    assert _plan_row(fixed, 2).endswith("| #1, #3 |")
    assert apply_inferred_dependency_fixes(fixed) == (fixed, [])

    fixed_empty, _ = apply_inferred_dependency_fixes(_dep_body(_projection_subs(), ["none", ""]))
    assert _plan_row(fixed_empty, 2).endswith("| #1 |")


def test_apply_inferred_dependency_fixes_skips_cycle_edges():
    from issuesmith.gate_rules.b1_milestone_subdesign import apply_inferred_dependency_fixes

    body = _dep_body(_cycle_subs(), ["none", "none"])
    assert apply_inferred_dependency_fixes(body) == (body, [])


def test_dependency_vocabulary_comes_from_language_pack(tmp_path, monkeypatch):
    """A non-Japanese pack: new kind, depends-on header and no-deps word are config values."""
    import dataclasses

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        apply_inferred_dependency_fixes,
        extract_sub_blocks,
        infer_sub_dependencies,
    )
    from issuesmith.language import EN

    data = {f.name: getattr(EN, f.name) for f in dataclasses.fields(EN)}
    data.update(
        sub_header_prefix=SUB,
        change_table_columns=[REPOSITORY, FILE_PATH, CHANGE_TYPE, DESCRIPTION],
        sub_plan_columns=["#", "Title", "Repo", "What", "Needs"],
        new_words=["create"],
        no_deps_word="-",
    )
    plain = {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}
    plain["sections"] = dict(EN.sections)
    plain["messages"] = dict(EN.messages)
    pack = tmp_path / "pack.yaml"
    pack.write_text(yaml.safe_dump(plain), encoding="utf-8")
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(yaml.safe_dump({"repo": _REPO, "language_pack": str(pack)}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    try:
        blocks = [
            _dep_sub(1, [("src/issuesmith/projection.py", "create", "c")]),
            _dep_sub(2, [("src/issuesmith/ops/dispatch.py", "modify", "use projection")]),
        ]
        body = _dep_body(blocks, ["-", "-"])
        assert "| Needs |" in body
        assert infer_sub_dependencies(extract_sub_blocks(body)) == {1: set(), 2: {1}}
        assert _dep_ids(body) == [_DEP_MISSING]
        fixed, _ = apply_inferred_dependency_fixes(body)
        assert _plan_row(fixed, 2).endswith("| #1 |")
        assert _plan_row(fixed, 1).endswith("| - |")
        # "add" is not a new word in this pack.
        blocks[0] = _dep_sub(1, [("src/issuesmith/projection.py", "add", "c")])
        assert _dep_ids(_dep_body(blocks, ["-", "-"])) == []
    finally:
        reset_config_cache()


# ---------------------------------------------------------------------------
# #4818 — ordering sentence without a depends-on entry
# ---------------------------------------------------------------------------

_ORDER_RULE = "b1_milestone_subdesign.sub_order_without_dependency"


def _order_body(sub2_policy: str, dep: str, *, sub2_ac: str = "Sub2 gamma") -> str:
    """Two-sub body whose Sub2 design policy is ``sub2_policy`` and depends-on is ``dep``."""
    body = _with_test_sub_dependency(
        _milestone_body([
            ("impl one", [("docs/a.md", "Modify")]),
            (sub2_policy, [("docs/b.md", "Modify")]),
        ]),
        dep,
    )
    return body.replace("- [ ] Sub2 gamma", f"- [ ] {sub2_ac}")


def _order_hits(body: str):
    return [v for v in _check(body, MILESTONE_LABELS) if v.rule_id == _ORDER_RULE]


def test_sub_order_without_dependency_detected():
    hits = _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "none"))
    assert len(hits) == 1
    assert hits[0].severity == "fail"
    assert hits[0].auto_fixable is False
    assert hits[0].location == f"#### {SUB}2"
    assert "Sub 2" in hits[0].message and "Sub 1" in hits[0].message
    assert "#1" in hits[0].fix_hint


def test_sub_order_with_dependency_passes():
    assert _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "#1")) == []


def test_sub_order_dependency_does_not_match_by_prefix():
    hits = _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "#12"))
    assert len(hits) == 1


def test_sub_order_same_sub_referenced_twice_is_one_violation():
    policy = f"Start after {SUB} 1 is merged; once {SUB}1 lands, wire it up."
    assert len(_order_hits(_order_body(policy, "none"))) == 1


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(f"{SUB} 2 runs after the config change.", id="self-reference"),
        pytest.param(f"Afterwards {SUB} 1 stays as is.", id="afterwards"),
        pytest.param(f"Reuse the helper from {SUB} 1.", id="no-order-word"),
    ],
)
def test_sub_order_not_detected(policy):
    assert _order_hits(_order_body(policy, "none")) == []


def test_sub_order_ignores_acceptance_criteria_and_tables():
    body = _order_body(
        "Plain change.", "none", sub2_ac=f"Works after {SUB} 1 is merged"
    )
    body = body.replace(
        "| `sumipan/nexus` | `docs/b.md` | Modify | Description |",
        f"| `sumipan/nexus` | `docs/b.md` | Modify | after {SUB} 1 |",
    )
    assert _order_hits(body) == []


def test_sub_order_skipped_without_depends_on_column():
    body = _milestone_body([
        ("impl one", [("docs/a.md", "Modify")]),
        (f"Start after {SUB} 1 is merged.", [("docs/b.md", "Modify")]),
    ])
    assert _order_hits(body) == []


def test_sub_order_words_come_from_language_pack(tmp_path, monkeypatch):
    _use_pack(
        tmp_path,
        monkeypatch,
        sub_header_prefix=SUB,
        change_table_columns=[REPOSITORY, FILE_PATH, CHANGE_TYPE, DESCRIPTION],
        order_after_words=["following"],
    )
    assert len(_order_hits(_order_body(f"Start following {SUB} 1.", "none"))) == 1
    assert _order_hits(_order_body(f"Start following {SUB} 1.", "#1")) == []
    assert _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "none")) == []


def test_sub_plan_dep_format_fixes_cases():
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        _SUB_PLAN_DEP_FORMAT_ID,
        apply_sub_plan_dep_format_fixes,
    )
    from tests.legacy_text import NONE

    def _plan_body(dep: str) -> str:
        return _dep_body(_projection_subs(), ["none", dep])

    natural_dep = "after sub2 merge"
    cases = [
        ("2", "#2"),
        ("4, 2", "#4, #2"),
        ("#2", "#2"),
        (NONE, NONE),
        (natural_dep, natural_dep),
    ]
    for dep, expected in cases:
        body = _plan_body(dep)
        fixed, applied = apply_sub_plan_dep_format_fixes(body)
        if dep in ("#2", NONE, natural_dep):
            assert applied == []
            assert dep in fixed
        else:
            assert applied == [_SUB_PLAN_DEP_FORMAT_ID]
            assert f"| 2 | part 2 | {_REPO} | c | {expected} |" in fixed
    once, first_applied = apply_sub_plan_dep_format_fixes(_plan_body("2"))
    assert first_applied == [_SUB_PLAN_DEP_FORMAT_ID]
    _, second_applied = apply_sub_plan_dep_format_fixes(once)
    assert second_applied == []


def test_sub_plan_dep_format_violation_auto_fixable():
    from issuesmith.gate_rules.b1_milestone_subdesign import _SUB_PLAN_DEP_FORMAT_ID

    bare = _dep_body(_projection_subs(), ["none", "2"])
    hits = [v for v in _check(bare, MILESTONE_LABELS) if v.rule_id == _SUB_PLAN_DEP_FORMAT_ID]
    assert len(hits) == 1 and hits[0].auto_fixable
    fixed = _dep_body(_projection_subs(), ["none", "#2"])
    assert not [v for v in _check(fixed, MILESTONE_LABELS) if v.rule_id == _SUB_PLAN_DEP_FORMAT_ID]
