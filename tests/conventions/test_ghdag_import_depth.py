"""Conventions: ghdag imports must use one-level public paths only."""
from __future__ import annotations

import ast
import importlib.util
import re
from typing import Iterable

import pytest

from tests.conventions._util import REPO_ROOT, py_files

_SHIM_SUFFIXES = ("exceptions", "github_client")
_SHIM_PREFIXES = tuple(f"ghdag.{s}" for s in _SHIM_SUFFIXES)
_GHDAG_DOTTED_RE = re.compile(r"^ghdag(?:\.\w+)+$")


def _is_shim_path(path: str) -> bool:
    return path in _SHIM_PREFIXES or any(
        path == p or path.startswith(p + ".") for p in _SHIM_PREFIXES
    )


def _import_module_too_deep(module: str | None) -> bool:
    if not module or module == "ghdag":
        return False
    if not module.startswith("ghdag."):
        return False
    if _is_shim_path(module):
        return True
    return len(module.split(".")) >= 3


def _string_ref_violation(value: str) -> bool:
    if not _GHDAG_DOTTED_RE.fullmatch(value):
        return False
    if _is_shim_path(value):
        return True
    parts = value.split(".")
    if len(parts) < 3:
        return False
    module_path = ".".join(parts[:3])
    try:
        spec = importlib.util.find_spec(module_path)
    except (ModuleNotFoundError, ValueError):
        return False
    return spec is not None


def deep_ghdag_refs(source: str) -> list[str]:
    """Return ``"<lineno>: <ref>"`` for each disallowed ghdag reference in *source*."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                if _import_module_too_deep(mod):
                    out.append(f"{node.lineno}: import {mod}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module
            if _import_module_too_deep(mod):
                out.append(f"{node.lineno}: from {mod}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            s = node.value
            if _string_ref_violation(s):
                out.append(f"{node.lineno}: {s!r}")
    return out


_SELF = REPO_ROOT / "tests" / "conventions" / "test_ghdag_import_depth.py"


def _scan_roots(roots: Iterable) -> list[str]:
    violations: list[str] = []
    for root in roots:
        for path in py_files(root):
            if path.resolve() == _SELF.resolve():
                continue
            rel = path.relative_to(REPO_ROOT)
            for item in deep_ghdag_refs(path.read_text(encoding="utf-8")):
                violations.append(f"{rel}:{item}")
    return sorted(violations)


def test_no_deep_ghdag_refs_in_repo():
    found = _scan_roots((REPO_ROOT / "src", REPO_ROOT / "tests"))
    assert found == [], "deep ghdag refs:\n" + "\n".join(found)


@pytest.mark.parametrize(
    "snippet",
    [
        "from ghdag.pipeline.state import PipelineState",
        "from ghdag.exceptions import GitHubApiError",
        "import ghdag.github_client",
        'importlib.util.find_spec("ghdag.dag.recover")',
    ],
)
def test_detects_deep_ghdag_ref(snippet: str):
    assert deep_ghdag_refs(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "from ghdag.forge import GitHubApiError",
        "from ghdag.io import done",
        'patch("ghdag.forge.get_forge", fake)',
        'x = "ghdag.quota.QuotaGate"',
    ],
)
def test_allows_one_level_ref(snippet: str):
    assert deep_ghdag_refs(snippet) == []
