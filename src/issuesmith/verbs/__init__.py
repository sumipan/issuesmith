"""issuesmith.verbs — public verb API extracted from step implementations."""

from __future__ import annotations

from issuesmith.engine import (
    exec_file,
    resolve,
    route,
    run_guarded,
    run_verified,
)
from issuesmith.ops.dispatch import map_step_result
from issuesmith.verbs.acceptance import (
    AcceptanceError,
    AcceptanceVerdict,
    ReviewFacts,
    check_acceptance,
    check_contract,
    extract_contract,
    review_tier,
    unchecked_acceptance_count,
)
from issuesmith.verbs.children import (
    ChildPlan,
    ChildPlanEntry,
    ChildPlanError,
    create_child,
    plan_children,
    validate_children,
    widen_allow_paths,
)
from issuesmith.verbs.finalize import cleanup_branches, cleanup_worktrees, close_issue
from issuesmith.verbs.merge import find_pr, merge_pr, merge_state
from issuesmith.verbs.publish import publish_branch
from issuesmith.verbs.worktree import (
    WorktreeError,
    block_design_only_issue,
    ensure_base_included,
    prepare_worktree,
    resolve_base_ref,
)

__all__ = [
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
    "create_child",
    "ensure_base_included",
    "exec_file",
    "extract_contract",
    "find_pr",
    "map_step_result",
    "merge_pr",
    "merge_state",
    "plan_children",
    "prepare_worktree",
    "publish_branch",
    "resolve",
    "resolve_base_ref",
    "route",
    "review_tier",
    "run_guarded",
    "run_verified",
    "unchecked_acceptance_count",
    "validate_children",
    "widen_allow_paths",
]
