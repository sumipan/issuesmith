"""Workflow design conventions — deterministic detector (#3487).

Rules (workflow design conventions):

R2 (contract fixtures) lives in tests/contract/: the same fixture goes through
    the gate rule and the consuming step, and both must agree.

R3 (PREFLIGHT_PARITY / assert_preflight_parity) was removed in #3628.
    Stop-status quality is now guaranteed structurally by the requires-chain
    validation introduced in #3626 / #3627.

R4  scope_coupling.ignore_symbols was removed in #3628. Config files that still
    carry the key must raise ConfigError so operators remove the dead key.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from issuesmith.config import ConfigError, load_config


def test_r4_scope_coupling_ignore_symbols_raises_config_error(tmp_path: Path) -> None:
    """R4: scope_coupling.ignore_symbols removed in #3628; stale config must raise ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        textwrap.dedent("""\
            repo: sumipan/issuesmith
            scope_coupling:
              ignore_symbols:
                - run_guarded
        """),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="ignore_symbols"):
        load_config(cfg_path)
