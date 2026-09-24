"""issuesmith.gates — unified gate public API with Verdict return type."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Protocol

__all__ = [
    "Verdict",
    "GateBuildContext",
    "GateBuildError",
    "GateEntry",
    "RequiresGate",
    "GATE_REGISTRY",
    "validate_step_requires",
    "check_scope",
    "check_pr_scope",
    "check_m2",
    "check_deps",
]

InputKind = Literal["issue", "worktree", "artifact"]


@dataclass(frozen=True)
class Verdict:
    """Uniform gate result: passed flag and human-readable reasons."""

    passed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GateBuildContext:
    """Context passed to GateEntry.build when instantiating a gate."""

    worktree_path: Path | None
    allow_paths: list[str]
    base_branch: str


class GateBuildError(ValueError):
    """Raised by GateEntry.build when the gate cannot be instantiated."""


class RequiresGate(Protocol):
    """Protocol: a gate that can be checked against issue body and labels."""

    def check(self, body: str, labels: list[str]) -> list[Any]: ...


@dataclass(frozen=True)
class GateEntry:
    """Registry entry describing a gate's input_kind and build factory."""

    input_kind: InputKind
    build: Callable[[GateBuildContext], RequiresGate]
    pre_llm: bool = False
    repairable: bool = True


def _require_worktree(gate_id: str, ctx: GateBuildContext) -> Path:
    if ctx.worktree_path is None:
        raise GateBuildError(
            f"gate {gate_id!r} requires worktree_path but context has none"
        )
    return ctx.worktree_path


def _build_registry() -> dict[str, GateEntry]:
    """Build the unified GATE_REGISTRY from gate modules and gate_rules."""
    from issuesmith.gates.dep import DepsGate
    from issuesmith.gates.pr_scope import PrScopeGate
    from issuesmith.gates.scope import ScopeGate
    from issuesmith.gates.worktree import WORKTREE_GATES

    registry: dict[str, GateEntry] = {}

    # Issue-kind gate: deps.
    registry["deps"] = GateEntry(
        input_kind="issue",
        build=lambda ctx: DepsGate(),
    )

    # Worktree gates defined in gates/worktree.py.
    # pre_llm=True: evaluated before LLM in run_guarded --requires-step
    # repairable=False: violation triggers andon(decision) instead of repair loop
    _PRE_LLM_GATES = frozenset({"base_freshness"})

    for gate_id, factory in WORKTREE_GATES.items():
        def _make_worktree_build(f=factory, gid=gate_id):
            def _build(ctx: GateBuildContext) -> RequiresGate:
                p = _require_worktree(gid, ctx)
                return f(p, ctx.allow_paths, ctx.base_branch)
            return _build
        registry[gate_id] = GateEntry(
            input_kind="worktree",
            build=_make_worktree_build(),
            pre_llm=gate_id in _PRE_LLM_GATES,
        )

    # scope and pr_scope: issue-authored adapters that wrap worktree operations.
    def _build_scope(ctx: GateBuildContext) -> RequiresGate:
        p = _require_worktree("scope", ctx)
        return ScopeGate(p, ctx.allow_paths)

    def _build_pr_scope(ctx: GateBuildContext) -> RequiresGate:
        p = _require_worktree("pr_scope", ctx)
        return PrScopeGate(p, ctx.allow_paths, ctx.base_branch)

    registry["scope"] = GateEntry(input_kind="worktree", build=_build_scope)
    registry["pr_scope"] = GateEntry(input_kind="worktree", build=_build_pr_scope)

    # Gate rules registered in ghdag's GATE_REGISTRY (via import issuesmith.gate_rules).
    from ghdag.workflow.gates import GATE_REGISTRY as _IMPL_REG

    import issuesmith.gate_rules  # noqa: F401 — triggers registration side effects

    def _make_rule_build(cls: Any) -> Callable[[GateBuildContext], RequiresGate]:
        def _build(ctx: GateBuildContext) -> RequiresGate:
            gate: RequiresGate = cls()
            return gate
        return _build

    # scope_breadth: pre_llm=True (checked before LLM), repairable=False (scope too large → split/reject)
    _ISSUE_PRE_LLM_NON_REPAIRABLE = frozenset({"scope_breadth"})

    for gate_id, cls in _IMPL_REG.items():
        if gate_id not in registry:
            registry[gate_id] = GateEntry(
                input_kind="issue",
                build=_make_rule_build(cls),
                pre_llm=gate_id in _ISSUE_PRE_LLM_NON_REPAIRABLE,
                repairable=gate_id not in _ISSUE_PRE_LLM_NON_REPAIRABLE,
            )

    return registry


# Build the registry at module load time.
GATE_REGISTRY: dict[str, GateEntry] = _build_registry()


# Import after registry is built to avoid circular-import issues.
from issuesmith.gates.dep import check_deps  # noqa: E402
from issuesmith.gates.m2 import check_m2  # noqa: E402
from issuesmith.gates.pr_scope import check_pr_scope  # noqa: E402
from issuesmith.gates.scope import check_scope  # noqa: E402


def validate_step_requires(steps: "Mapping[str, Any]") -> None:
    """Validate ``StepConfig.requires`` against GATE_REGISTRY (unknown ids, input_kind rules).

    Kept out of ``config.load_config`` so loading the config never imports the gate registry
    (sumipan/nexus#3687). Raises ``issuesmith.config.ConfigError`` with the same messages the
    loader used to raise. Call from doctor, dispatch and ``config show``.
    """
    from issuesmith.config import ConfigError  # noqa: PLC0415 - config must not import gates

    known = sorted(GATE_REGISTRY)
    for step_id, step in steps.items():
        requires = tuple(getattr(step, "requires", ()) or ())
        if not requires:
            continue
        unknown = [g for g in requires if g not in GATE_REGISTRY]
        if unknown:
            raise ConfigError(
                f"steps.{step_id}.requires contains unknown gate ids"
                f" (missing gate ids: {unknown}). Known ids: {known}"
            )
        input_kind = getattr(step, "input_kind", "issue")
        for gate_id in requires:
            gate_input_kind = GATE_REGISTRY[gate_id].input_kind
            if gate_input_kind == "worktree" and input_kind != "worktree":
                raise ConfigError(
                    f"steps.{step_id}: worktree gate {gate_id!r} can only be used"
                    f" in worktree steps, but step input_kind={input_kind!r}"
                )
            if gate_input_kind == "artifact" and input_kind != "artifact":
                raise ConfigError(
                    f"steps.{step_id}: artifact gate {gate_id!r} can only be used"
                    f" in artifact steps, but step input_kind={input_kind!r}"
                )
            if gate_input_kind == "issue" and input_kind == "artifact":
                raise ConfigError(
                    f"steps.{step_id}: issue gate {gate_id!r} cannot be used"
                    f" in artifact steps"
                )
