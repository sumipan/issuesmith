"""Workflow design conventions — deterministic detector (#3487).

Rules (nexus docs/ISSUESMITH.md, section "Workflow design conventions"):

R1  A contract section has exactly one parser. Any ``def`` whose name is in
    ``issuesmith.contract.CONTRACT_EXTRACTORS`` must live in ``contract.py``.
    Gates and steps import it; they never re-implement it.
R3  Every runtime stop status (``pipeline_status="..._FAILED"`` etc. in
    ``steps/``) is listed in ``gate_rules.PREFLIGHT_PARITY`` with either the
    gate rule_id that catches the same defect before dispatch or an explicit
    ``runtime-only:`` reason. Mapped rule_ids must exist.
R2 (contract fixtures) lives in tests/contract/: the same fixture goes through
    the gate rule and the consuming step, and both must agree.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from issuesmith.contract import CONTRACT_EXTRACTORS
from issuesmith.gate_rules import PREFLIGHT_PARITY, STOP_STATUS_SUFFIXES, assert_preflight_parity

_SRC = Path(__file__).resolve().parents[1] / "src" / "issuesmith"
_CANONICAL = _SRC / "contract.py"
_STOP_SUFFIXES = STOP_STATUS_SUFFIXES


def _iter_src() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


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


def _stop_statuses_in_steps() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    pattern = re.compile(r'pipeline_status="([A-Z0-9_]+)"|PIPELINE_STATUS:\s*([A-Z0-9_]+)')
    for path in sorted((_SRC / "steps").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            status = match.group(1) or match.group(2)
            if status.endswith(_STOP_SUFFIXES):
                found.setdefault(status, []).append(path.name)
    return found


def _known_rule_ids() -> set[str]:
    ids: set[str] = set()
    for path in (_SRC / "gate_rules").glob("*.py"):
        ids.update(re.findall(r'rule_id="([a-z0-9_.]+)"', path.read_text(encoding="utf-8")))
    return ids


def test_r3_every_runtime_stop_has_a_preflight_entry() -> None:
    stops = _stop_statuses_in_steps()
    missing = sorted(s for s in stops if s not in PREFLIGHT_PARITY)
    assert missing == [], (
        "R3 violation: runtime stop status without a preflight parity entry "
        "(add it to issuesmith.gate_rules.PREFLIGHT_PARITY with the gate rule_id "
        "or a 'runtime-only:' reason):\n"
        + "\n".join(f"{s} ({', '.join(stops[s])})" for s in missing)
    )


def test_r3_parity_targets_exist() -> None:
    rule_ids = _known_rule_ids()
    bad = sorted(
        f"{status} -> {target}"
        for status, target in PREFLIGHT_PARITY.items()
        if not target.startswith("runtime-only:") and target not in rule_ids
    )
    assert bad == [], "R3 violation: PREFLIGHT_PARITY points at unknown rule_id:\n" + "\n".join(bad)


def test_r3_parity_has_no_stale_entries() -> None:
    stops = set(_stop_statuses_in_steps())
    stale = sorted(s for s in PREFLIGHT_PARITY if s not in stops)
    assert stale == [], "PREFLIGHT_PARITY lists statuses no step emits:\n" + "\n".join(stale)


def test_r3_step_result_refuses_unregistered_stop_status() -> None:
    """Structural guard: StepResult cannot be built with an unknown stop status."""
    import pytest

    from issuesmith.steps.base import StepResult

    with pytest.raises(ValueError, match="preflight parity"):
        StepResult(exit_code=1, pipeline_status="SOMETHING_NEW_FAILED")
    assert StepResult(exit_code=0, pipeline_status="WORKTREE_READY").exit_code == 0
    assert_preflight_parity("SCOPE_TOO_LARGE")
