"""Compatibility shim — implementation lives in milestone_subdesign_rules."""

from __future__ import annotations

import sys

import issuesmith.gate_rules.milestone_subdesign_rules as _impl

sys.modules[__name__] = _impl
