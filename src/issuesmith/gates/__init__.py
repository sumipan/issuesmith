"""issuesmith.gates — unified gate public API with Verdict return type."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "Verdict",
    "check_scope",
    "check_pr_scope",
    "check_m2",
    "check_deps",
]


@dataclass(frozen=True)
class Verdict:
    """Uniform gate result: passed flag and human-readable reasons."""

    passed: bool
    reasons: list[str] = field(default_factory=list)


# Import after Verdict is defined to avoid circular-import issues.
from issuesmith.gates.dep import check_deps  # noqa: E402
from issuesmith.gates.m2 import check_m2  # noqa: E402
from issuesmith.gates.pr_scope import check_pr_scope  # noqa: E402
from issuesmith.gates.scope import check_scope  # noqa: E402
