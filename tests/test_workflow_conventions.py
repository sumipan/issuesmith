"""Workflow design conventions — deterministic detector (#3487).

Rules (nexus docs/ISSUESMITH.md, section "Workflow design conventions"):

R1  A contract section has exactly one parser. Any ``def`` whose name is in
    ``issuesmith.contract.CONTRACT_EXTRACTORS`` must live in ``contract.py``.
    Gates and steps import it; they never re-implement it.
R2 (contract fixtures) lives in tests/contract/: the same fixture goes through
    the gate rule and the consuming step, and both must agree.

R3 (PREFLIGHT_PARITY / assert_preflight_parity) was removed in #3628.
    Stop-status quality is now guaranteed structurally by the requires-chain
    validation introduced in #3626 / #3627.

R4  scope_coupling.ignore_symbols was removed in #3628. Config files that still
    carry the key must raise ConfigError so operators remove the dead key.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from issuesmith.config import ConfigError, load_config
from issuesmith.contract import CONTRACT_EXTRACTORS

_SRC = Path(__file__).resolve().parents[1] / "src" / "issuesmith"
_CANONICAL = _SRC / "contract.py"


def _iter_src() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


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


def test_r1_contract_extractors_are_defined_once() -> None:
    duplicates: list[str] = []
    for path in _iter_src():
        if path == _CANONICAL:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in CONTRACT_EXTRACTORS:
                    duplicates.append(f"{path.relative_to(_SRC)}:{node.lineno} def {node.name}")
    assert duplicates == [], (
        "R1 violation: contract extractor re-implemented outside contract.py "
        "(import it from issuesmith.contract instead):\n" + "\n".join(duplicates)
    )
