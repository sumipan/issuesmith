"""__all__ names resolve; private cross-module imports are a fixed, shrinking set."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from tests.conventions._util import SRC, import_froms, module_name, py_files

KNOWN_FILE = Path(__file__).with_name("known_private_imports.txt")


def _known_private_imports() -> frozenset[tuple[str, str]]:
    """Known private cross-module imports (``module name`` per line). Shrink; never grow."""
    pairs = set()
    for line in KNOWN_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            module, name = line.split()
            pairs.add((module, name))
    return frozenset(pairs)


def _modules_with_all() -> list[str]:
    return [module_name(p) for p in py_files(SRC) if "__all__" in p.read_text(encoding="utf-8")]


@pytest.mark.parametrize("modname", _modules_with_all())
def test_all_names_resolve(modname: str):
    mod = importlib.import_module(modname)
    for name in getattr(mod, "__all__", ()):
        assert hasattr(mod, name), f"{modname}.__all__ names missing attribute {name}"


def _found_private_imports() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in py_files(SRC):
        this = module_name(path)
        for module, name, _ in import_froms(path):
            if not name.startswith("_") or name.startswith("__") or module == this:
                continue
            found.add((module, name))
    return found


def test_private_cross_module_imports_do_not_grow():
    found = _found_private_imports()
    known = _known_private_imports()
    new = found - known
    assert not new, f"new private cross-module imports (use a public name or add a public API): {sorted(new)}"
    gone = known - found
    assert not gone, f"known_private_imports.txt has stale entries, remove them: {sorted(gone)}"
