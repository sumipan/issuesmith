"""m2_gate.py — compatibility shim for issuesmith.ac_complete_entry."""

from __future__ import annotations

import sys

import issuesmith.ac_complete_entry as _entry

sys.modules[__name__] = _entry
