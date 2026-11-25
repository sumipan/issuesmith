"""Public export contract for issuesmith.verbs (sumipan/nexus#6560)."""
from __future__ import annotations

import issuesmith.verbs as verbs_mod
from issuesmith import verbs as issuesmith_verbs
from issuesmith.engine import (
    exec_file as engine_exec_file,
)
from issuesmith.engine import (
    resolve as engine_resolve,
)
from issuesmith.engine import (
    route as engine_route,
)
from issuesmith.engine import (
    run_guarded as engine_run_guarded,
)
from issuesmith.engine import (
    run_verified as engine_run_verified,
)
from issuesmith.gates.worktree import line_has_cjk
from issuesmith.ops.dispatch import map_step_result as dispatch_map_step_result
from issuesmith.ops.publish import PublishResult as ops_publish_result
from issuesmith.verbs import acceptance as acceptance_mod
from issuesmith.verbs import children as children_mod
from issuesmith.verbs import publish as publish_mod
from issuesmith.verbs import worktree as worktree_mod
from tests.conventions._util import REPO_ROOT

VERBS_PUBLIC_NAMES = frozenset(
    {
        "AcceptanceError",
        "AcceptanceVerdict",
        "ReviewFacts",
        "ChildPlan",
        "ChildPlanEntry",
        "ChildPlanError",
        "WorktreeError",
        "block_design_only_issue",
        "check_acceptance",
        "check_contract",
        "cleanup_branches",
        "cleanup_worktrees",
        "close_issue",
        "read_result",
        "create_child",
        "ensure_base_included",
        "exec_file",
        "extract_contract",
        "find_pr",
        "map_step_result",
        "merge_pr",
        "merge_state",
        "OrderRun",
        "PublishResult",
        "plan_children",
        "pr_title_and_body",
        "publish_pr",
        "pre_llm_violations",
        "prepare_worktree",
        "RebumpResult",
        "publish_branch",
        "rebump_onto_base",
        "resolve",
        "resolve_base_ref",
        "route",
        "review_tier",
        "run_guarded",
        "run_order",
        "run_verified",
        "unchecked_acceptance_count",
        "validate_children",
        "widen_allow_paths",
        "write_result",
    }
)


def test_verbs_public_exports():
    """issuesmith.verbs __all__ matches the contract; re-exports share definition objects."""
    assert frozenset(verbs_mod.__all__) == VERBS_PUBLIC_NAMES
    for name in VERBS_PUBLIC_NAMES:
        assert hasattr(issuesmith_verbs, name), f"verbs missing attribute {name}"
    assert issuesmith_verbs.run_guarded is engine_run_guarded
    assert issuesmith_verbs.run_verified is engine_run_verified
    assert issuesmith_verbs.exec_file is engine_exec_file
    assert issuesmith_verbs.resolve is engine_resolve
    assert issuesmith_verbs.route is engine_route
    assert issuesmith_verbs.map_step_result is dispatch_map_step_result
    assert issuesmith_verbs.WorktreeError is worktree_mod.WorktreeError
    assert issuesmith_verbs.prepare_worktree is worktree_mod.prepare_worktree
    assert issuesmith_verbs.ensure_base_included is worktree_mod.ensure_base_included
    assert issuesmith_verbs.block_design_only_issue is worktree_mod.block_design_only_issue
    assert issuesmith_verbs.resolve_base_ref is worktree_mod.resolve_base_ref
    assert issuesmith_verbs.check_acceptance is acceptance_mod.check_acceptance
    assert issuesmith_verbs.AcceptanceError is acceptance_mod.AcceptanceError
    assert issuesmith_verbs.plan_children is children_mod.plan_children
    assert issuesmith_verbs.ChildPlanError is children_mod.ChildPlanError
    assert issuesmith_verbs.PublishResult is ops_publish_result
    assert issuesmith_verbs.publish_pr is publish_mod.publish_pr
    assert issuesmith_verbs.pr_title_and_body is publish_mod.pr_title_and_body


def test_public_api_docs_are_english():
    """CHANGELOG Unreleased docs for the public API contain no CJK."""
    path = REPO_ROOT / "CHANGELOG.md"
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        assert not line_has_cjk(line), f"CHANGELOG.md:{line_no}: CJK in public API docs"
