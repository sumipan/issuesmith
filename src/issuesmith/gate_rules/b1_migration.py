"""Compatibility shim — implementation lives in migration_plan_rules."""

from __future__ import annotations

import sys

import issuesmith.gate_rules.migration_plan_rules as _impl

sys.modules[__name__] = _impl
