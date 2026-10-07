"""Config-driven phase advance predicates (#4790)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ghdag.forge import ForgePort

from issuesmith.config import IssuesmithConfig, PhaseConfig

Predicate = Callable[["PreconditionContext", IssuesmithConfig], tuple[bool, str]]

PRECONDITION_REGISTRY: dict[str, Predicate] = {}


def register(name: str, fn: Predicate) -> None:
    if name in PRECONDITION_REGISTRY:
        raise ValueError(f"predicate already registered: {name!r}")
    PRECONDITION_REGISTRY[name] = fn


@dataclass(frozen=True)
class PreconditionContext:
    issue: dict[str, Any]
    labels: set[str]
    client: ForgePort
    issue_number: int


def evaluate(
    phase: PhaseConfig,
    ctx: PreconditionContext,
    config: IssuesmithConfig,
) -> tuple[bool, str]:
    state = str(ctx.issue.get("state", "")).upper()
    if state != "OPEN":
        return False, "issue not OPEN"

    ready, running, done = config.phase_labels(phase.name)
    for lab in (ready, running, done):
        if lab in ctx.labels:
            return False, f"{lab} present"

    for req in config.required_labels(phase.name):
        if req not in ctx.labels:
            return False, f"{req} required"

    for excl in config.excluded_labels(phase.name):
        if excl in ctx.labels:
            return False, f"{excl} excluded"

    for pred_name in phase.advance_when:
        fn = PRECONDITION_REGISTRY.get(pred_name)
        if fn is None:
            return False, f"unknown predicate {pred_name}"
        ok, why = fn(ctx, config)
        if not ok:
            return False, why
        if why == "already_merged":
            return True, "already_merged"

    return True, "ok"


def _deps_terminal(ctx: PreconditionContext, config: IssuesmithConfig) -> tuple[bool, str]:
    from issuesmith.dep_extractor import (
        check_dependencies,
        extract_dependencies,
        unparsed_dependency_refs,
    )

    body = str(ctx.issue.get("body") or "")
    unparsed = unparsed_dependency_refs(body)
    if unparsed:
        refs = ", ".join(f"#{n}" for n in unparsed)
        return False, f"dependencies section mentions {refs} without declaring them"
    deps = extract_dependencies(body)
    if deps:
        result = check_dependencies(deps, client=ctx.client)
        if result.decision == "BLOCK":
            return False, "dependencies not satisfied"
    return True, "ok"


def _closing_pr_exists(ctx: PreconditionContext, config: IssuesmithConfig) -> tuple[bool, str]:
    from issuesmith.queue import (
        _find_merged_prs_closing_issue,
        _find_open_prs_closing_issue,
    )

    matched = _find_open_prs_closing_issue(ctx.client, ctx.issue_number)
    if matched:
        return True, "ok"
    merged = _find_merged_prs_closing_issue(ctx.client, ctx.issue_number)
    merge_done = ""
    for ph in config.phases:
        if "closing_pr_exists" in ph.advance_when:
            _, _, merge_done = config.phase_labels(ph.name)
            break
    if merged and merge_done and merge_done not in ctx.labels:
        return True, "already_merged"
    return False, "no open or merged PR with Closes #N"


register("deps_terminal", _deps_terminal)
register("closing_pr_exists", _closing_pr_exists)
