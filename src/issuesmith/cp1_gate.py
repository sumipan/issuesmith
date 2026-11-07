"""cp1_gate.py — compatibility shim for issuesmith.design_lint_entry."""

from __future__ import annotations

import sys

import issuesmith.design_lint_entry as _entry

sys.modules[__name__] = _entry
