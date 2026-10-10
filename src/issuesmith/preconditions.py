"""Config-driven phase advance predicates (#4790)."""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable

from ghdag.forge import ForgePort

from issuesmith.config import ConfigError, IssuesmithConfig, PhaseConfig

Predicate = Callable[["PreconditionContext", IssuesmithConfig], tuple[bool, str]]

PRECONDITION_REGISTRY: dict[str, Predicate] = {}

_EXTERNAL_REFERENCE_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")


def external_reference_valid(name: str) -> bool:
    """Return True if name matches module.path:attr external reference syntax."""
    return _EXTERNAL_REFERENCE_RE.fullmatch(name) is not None


def resolve_predicate(name: str) -> Predicate:
    """Resolve a registry key or module.path:attr to a predicate callable."""
    if ":" not in name:
        fn = PRECONDITION_REGISTRY.get(name)
        if fn is None:
            raise ConfigError(f"unknown predicate reference {name!r}")
        return fn

    if not external_reference_valid(name):
        raise ConfigError(f"invalid external predicate reference {name!r}")

    module_path, attr = name.split(":", 1)
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        mod_name = getattr(exc, "name", module_path)
        raise ConfigError(
            f"cannot resolve predicate {name!r}: module {mod_name!r} not found"
        ) from exc

    try:
        obj = getattr(mod, attr)
    except AttributeError:
        raise ConfigError(
            f"cannot resolve predicate {name!r}: attribute {attr!r} not found"
        )

    if not callable(obj):
        raise ConfigError(
            f"cannot resolve predicate {name!r}: attribute {attr!r} is not callable"
        )
    return obj  # type: ignore[return-value]


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
        try:
            fn = resolve_predicate(pred_name)
        except ConfigError as exc:
            return False, f"unknown predicate {pred_name}: {exc}"
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
    unparsed = unparsed_dependency_refs(body, self_issue=ctx.issue_number)
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
