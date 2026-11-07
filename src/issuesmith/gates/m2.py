"""gates/m2.py — compatibility shim for issuesmith.gates.ac_complete."""

from __future__ import annotations

import sys

import issuesmith.gates.ac_complete as _entry

sys.modules[__name__] = _entry
