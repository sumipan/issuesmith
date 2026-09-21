"""M2 gate — wraps m2_gate.check_gate to return unified Verdict."""

from __future__ import annotations

from pathlib import Path

from issuesmith.gates import Verdict
from issuesmith.m2_gate import check_gate


def check_m2(
    body: str,
    labels: list[str],
    repo_root: Path | None = None,
) -> Verdict:
    """Run M2 acceptance-criteria gate and return Verdict."""
    result = check_gate(body, labels, repo_root=repo_root)
    if result["action"] == "proceed":
        return Verdict(passed=True, reasons=[])
    reasons: list[str] = list(result.get("contract_failures") or [])
    if not reasons:
        unchecked = result.get("unchecked_count", 0)
        if unchecked:
            reasons = [f"unchecked acceptance criteria: {unchecked}"]
        else:
            reasons = [result["action"]]
    return Verdict(passed=False, reasons=reasons)


__all__ = ["check_m2"]
