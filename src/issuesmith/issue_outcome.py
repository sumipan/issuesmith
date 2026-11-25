"""Single definition of whether an Issue has finished (close reason only)."""

from __future__ import annotations

from typing import Any, Literal, Mapping

Outcome = Literal["open", "completed", "not_planned"]


def issue_outcome(issue: Mapping[str, Any]) -> Outcome:
    """Classify an issue_get payload by ``state`` and optional ``state_reason``."""
    state = str(issue.get("state", "")).casefold()
    if state != "closed":
        return "open"
    reason = issue.get("state_reason")
    if reason is not None:
        reason = str(reason).casefold()
        if reason in ("not_planned", "duplicate"):
            return "not_planned"
    return "completed"
