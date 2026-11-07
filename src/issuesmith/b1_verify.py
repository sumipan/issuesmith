"""Compatibility shim — design_doc verify logic lives in step_verify."""

from __future__ import annotations

from issuesmith.step_verify import (
    DESIGN_DOC_GATES,
    _parse_prev_counts,
    apply_deterministic_recovery,
    assumed_labels,
    check_paths_must_not_exist_validity,
    collect_violations,
    detect_oscillation,
    format_report,
)

_GATES = DESIGN_DOC_GATES

__all__ = [
    "DESIGN_DOC_GATES",
    "_GATES",
    "_parse_prev_counts",
    "apply_deterministic_recovery",
    "assumed_labels",
    "check_paths_must_not_exist_validity",
    "collect_violations",
    "detect_oscillation",
    "format_report",
]
