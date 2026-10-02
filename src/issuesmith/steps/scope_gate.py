"""Deprecated compat re-exports — import from issuesmith.scope_gate instead (#4276)."""

from __future__ import annotations

import warnings

from issuesmith.scope_gate import (
    ScopeMeasure,
    ScopeVerdict,
    evaluate,
    format_comment,
    measure_scope,
    override_from_metadata,
    parse_allow_paths_from_ctx,
    record_p0_trip_metric,
    resolve_scope_root,
)

__all__ = [
    "ScopeMeasure",
    "ScopeVerdict",
    "evaluate",
    "format_comment",
    "measure_scope",
    "override_from_metadata",
    "parse_allow_paths_from_ctx",
    "record_p0_trip_metric",
    "resolve_scope_root",
]

warnings.warn(
    "issuesmith.steps.scope_gate is deprecated; use issuesmith.scope_gate instead",
    DeprecationWarning,
    stacklevel=2,
)
