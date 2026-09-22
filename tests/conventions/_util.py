"""Shared helpers for the structural (conventions) tests — no mocks, no network."""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "issuesmith"
PKG = "issuesmith"


def py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT / "src").with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def import_froms(path: Path) -> list[tuple[str, str, int]]:
    """Return (module, name, lineno) for every `from X import Y` in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                out.append((node.module, alias.name, node.lineno))
    return out


_BACKTICK_CMD = re.compile(r"`(?:python3? -m )?issuesmith ([a-z][a-z0-9-]*)(?: [^`]*)?`")


def mentioned_commands(text: str) -> set[str]:
    """Commands named as `issuesmith <cmd> ...` / `python3 -m issuesmith <cmd> ...` in backticks."""
    return set(_BACKTICK_CMD.findall(text))
