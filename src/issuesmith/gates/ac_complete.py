"""AC-complete gate — wraps ac_complete_entry.check_gate to return unified Verdict."""

from __future__ import annotations

from pathlib import Path

from issuesmith.ac_complete_entry import check_gate
from issuesmith.gates import Verdict


def check_ac_complete(
    body: str,
    labels: list[str],
    repo_root: Path | None = None,
) -> Verdict:
    """Run acceptance-criteria completion gate and return Verdict."""
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


__all__ = ["check_ac_complete", "check_gate"]
