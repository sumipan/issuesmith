"""Deprecated compat alias — import from issuesmith.ops.repair_step instead (#4276).

The module object is replaced with ``issuesmith.ops.repair_step`` itself, so
callers that patch ``issuesmith.steps.repair.run_guarded`` / ``get_config`` /
``_get_previous_commits`` still patch the code that ``run`` executes.
"""

from __future__ import annotations

import sys
import warnings

from issuesmith.ops import repair_step as _impl

warnings.warn(
    "issuesmith.steps.repair is deprecated; use issuesmith.ops.repair_step instead",
    DeprecationWarning,
    stacklevel=2,
)

sys.modules[__name__] = _impl
