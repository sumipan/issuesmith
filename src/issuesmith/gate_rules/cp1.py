"""Compatibility shim — implementation lives in design_lint_rules."""

from __future__ import annotations

import sys

import issuesmith.gate_rules.design_lint_rules as _impl

sys.modules[__name__] = _impl
