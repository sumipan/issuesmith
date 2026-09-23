"""issuesmith.gates — unified gate public API with Verdict return type."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "Verdict",
    "GateEntry",
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
class GateEntry:
    """Registry entry describing a gate's input_kind."""

    input_kind: InputKind


# Registry maps gate id → GateEntry. Worktree gates receive (worktree_path, base_ref);
# issue gates receive issue body / numbers. Artifact gates do not touch GitHub at all.
GATE_REGISTRY: dict[str, GateEntry] = {
    "m2": GateEntry(input_kind="issue"),
    "deps": GateEntry(input_kind="issue"),
    "scope": GateEntry(input_kind="worktree"),
    "pr_scope": GateEntry(input_kind="worktree"),
}


# Import after Verdict is defined to avoid circular-import issues.
from issuesmith.gates.dep import check_deps  # noqa: E402
from issuesmith.gates.m2 import check_m2  # noqa: E402
from issuesmith.gates.pr_scope import check_pr_scope  # noqa: E402
from issuesmith.gates.scope import check_scope  # noqa: E402
