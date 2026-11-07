"""Compatibility shim — implementation lives in ac_format_rules."""

from __future__ import annotations

import sys

import issuesmith.gate_rules.ac_format_rules as _impl

sys.modules[__name__] = _impl
