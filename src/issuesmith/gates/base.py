"""Gate protocol and ContractInput for requires evaluation (#3626)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ContractInput:
    """Input passed to gate.check() and gate.fix() during requires evaluation."""

    body: str
    labels: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)


@runtime_checkable
class Gate(Protocol):
    """Protocol for a gate with an optional deterministic narrowing fix.

    fix() may only narrow (remove files, apply ruff --fix, fast-forward a branch);
    it must never widen allow_paths or add new scope.
    """

    def check(self, body: str, labels: list[str]) -> list:
        """Evaluate the gate; return list of Violation objects."""
        ...

    def fix(self, inp: ContractInput) -> ContractInput:
        """Return a deterministically fixed ContractInput.

        Default implementation returns inp unchanged.
        """
        return inp


__all__ = ["ContractInput", "Gate"]
