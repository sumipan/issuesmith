"""Deprecated compat re-exports — import from issuesmith.milestone instead (#4276)."""

from __future__ import annotations

import warnings

from ghdag.forge import ForgePort, get_forge

from issuesmith.config import StepConfig
from issuesmith.contract import StepContext, StepResult
from issuesmith.milestone import (
    PlanRow,
    Sub1State,
    _list_chain_children,
    _parse_table_rows,
    _plan_section,
    allow_paths_for_row,
    build_child_body,
    check_v1_target_repo,
    check_v2_allow_paths,
    check_v3_cjk_placeholders,
    ensure_sub1_binding,
    parse_split_plan,
    prevalidate_child_body,
    resolve_dependencies,
    run_guarded_sub1_body,
    run_sub1_create,
    validate_children,
)

__all__ = [
    "PlanRow",
    "Sub1State",
    "allow_paths_for_row",
    "build_child_body",
    "check_v1_target_repo",
    "check_v2_allow_paths",
    "check_v3_cjk_placeholders",
    "ensure_sub1_binding",
    "parse_split_plan",
    "prevalidate_child_body",
    "resolve_dependencies",
    "run",
    "validate_children",
]

warnings.warn(
    "issuesmith.steps.sub1_create is deprecated; use issuesmith.milestone instead",
    DeprecationWarning,
    stacklevel=2,
)

# Backward-compatible private aliases used by existing tests and callers.
_COMPAT_PRIVATE = (_list_chain_children, _parse_table_rows, _plan_section)
_parse_split_plan = parse_split_plan
_resolve_dependencies = resolve_dependencies
_allow_paths_for_row = allow_paths_for_row
_build_child_body = build_child_body
_prevalidate_child_body = prevalidate_child_body
_run_guarded_body = run_guarded_sub1_body


def _github_client() -> ForgePort:
    return get_forge()


def _create_child_issue(
    client: ForgePort,
    *,
    title: str,
    body: str,
    labels: list[str] | None,
    milestone: int | None,
) -> int:
    return int(client.issue_create(title, body, labels=labels, milestone=milestone))


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    return run_sub1_create(ctx, step)
