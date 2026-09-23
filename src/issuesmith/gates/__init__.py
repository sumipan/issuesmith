"""issuesmith.gates — unified gate public API with Verdict return type."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

__all__ = [
    "Verdict",
    "GateBuildContext",
    "GateBuildError",
    "GateEntry",
    "RequiresGate",
    "GATE_REGISTRY",
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


class RequiresGate:
    """Protocol: a gate that can be checked against issue body and labels."""

    def check(self, body: str, labels: list[str]) -> list:
        raise NotImplementedError


@dataclass(frozen=True)
class GateEntry:
    """Registry entry describing a gate's input_kind and build factory."""

    input_kind: InputKind
    build: Callable[[GateBuildContext], RequiresGate]


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
    for gate_id, factory in WORKTREE_GATES.items():
        def _make_worktree_build(f=factory, gid=gate_id):
            def _build(ctx: GateBuildContext) -> RequiresGate:
                p = _require_worktree(gid, ctx)
                return f(p, ctx.allow_paths, ctx.base_branch)
            return _build
        registry[gate_id] = GateEntry(
            input_kind="worktree",
            build=_make_worktree_build(),
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

    for gate_id, cls in _IMPL_REG.items():
        if gate_id not in registry:
            _cls = cls
            _gid = gate_id
            registry[_gid] = GateEntry(
                input_kind="issue",
                build=lambda ctx, c=_cls: c(),
            )

    return registry


# Build the registry at module load time.
GATE_REGISTRY: dict[str, GateEntry] = _build_registry()


# Import after registry is built to avoid circular-import issues.
from issuesmith.gates.dep import check_deps  # noqa: E402
from issuesmith.gates.m2 import check_m2  # noqa: E402
from issuesmith.gates.pr_scope import check_pr_scope  # noqa: E402
from issuesmith.gates.scope import check_scope  # noqa: E402
