"""M2 gate shim — re-exports ac-complete gate under legacy module path."""

from __future__ import annotations

from issuesmith.ac_complete_entry import check_gate
from issuesmith.gates.ac_complete import check_ac_complete

check_m2 = check_ac_complete

__all__ = ["check_m2", "check_ac_complete", "check_gate"]
