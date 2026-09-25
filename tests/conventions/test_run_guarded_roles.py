"""Every literal role handed to engine.run_guarded must be a role the engine state knows.

Regression: steps/repair.py passed "implement" while the engine state only has
"design" / "implementation", so the requires repair loop crashed with KeyError on its
first real use (P0 of a cross-repo Issue, 2026-09-25) and the step exited 1 silently.
"""
from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "issuesmith"
KNOWN_ROLES = {"design", "implementation"}


def _literal_roles(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "run_guarded" or not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            found.append((node.lineno, first.value))
    return found


def test_run_guarded_literal_roles_are_known() -> None:
    offenders = []
    for path in SRC.rglob("*.py"):
        for lineno, role in _literal_roles(path):
            if role not in KNOWN_ROLES:
                offenders.append(f"{path.relative_to(SRC)}:{lineno}: {role!r}")
    assert not offenders, "unknown engine roles passed to run_guarded: " + ", ".join(offenders)
