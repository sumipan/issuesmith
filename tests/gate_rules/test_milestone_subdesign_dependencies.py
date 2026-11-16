"""b1_milestone_subdesign: sub dependency inference, ordering and cycles."""

from __future__ import annotations

import pytest

import issuesmith.gate_rules.milestone_subdesign_rules  # noqa: F401
from tests.gate_rules.test_milestone_subdesign import (
    MILESTONE_LABELS,
    _check,
    _cycle_subs,
    _dep_body,
    _dep_sub,
    _milestone_body,
    _projection_subs,
    _with_test_sub_dependency,
)
from tests.legacy_text import SUB

# --- #4825: sibling new-file dependencies -------------------------------------------

_DEP_MISSING = "milestone_subdesign.sibling_new_file_unreferenced_dependency"
_DEP_CYCLE = "milestone_subdesign.dependency_cycle"


def _dep_ids(body: str) -> list[str]:
    return [v.rule_id for v in _check(body, MILESTONE_LABELS) if v.rule_id.startswith(
        (_DEP_MISSING, _DEP_CYCLE)
    )]





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



def test_declared_self_dependency_is_a_cycle():
    body = _dep_body(_projection_subs(), ["#1", "#1"])
    assert _dep_ids(body) == [_DEP_CYCLE]


def test_declared_edge_closing_cycle_is_reported():
    body = _dep_body(_projection_subs(), ["#2", "none"])
    assert _dep_ids(body) == [_DEP_CYCLE]




def test_apply_inferred_dependency_fixes_skips_cycle_edges():
    from issuesmith.gate_rules.milestone_subdesign_rules import apply_inferred_dependency_fixes

    body = _dep_body(_cycle_subs(), ["none", "none"])
    assert apply_inferred_dependency_fixes(body) == (body, [])



# ---------------------------------------------------------------------------
# #4818 — ordering sentence without a depends-on entry
# ---------------------------------------------------------------------------

_ORDER_RULE = "milestone_subdesign.sub_order_without_dependency"


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



def test_sub_order_with_dependency_passes():
    assert _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "#1")) == []


def test_sub_order_dependency_does_not_match_by_prefix():
    hits = _order_hits(_order_body(f"Start after {SUB} 1 is merged.", "#12"))
    assert len(hits) == 1


def test_sub_order_same_sub_referenced_twice_is_one_violation():
    policy = f"Start after {SUB} 1 is merged; once {SUB}1 lands, wire it up."
    assert len(_order_hits(_order_body(policy, "none"))) == 1




def test_sub_order_skipped_without_depends_on_column():
    body = _milestone_body([
        ("impl one", [("docs/a.md", "Modify")]),
        (f"Start after {SUB} 1 is merged.", [("docs/b.md", "Modify")]),
    ])
    assert _order_hits(body) == []




