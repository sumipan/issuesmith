"""Bundled reference production lines and file extract helpers."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

LINES: tuple[str, ...] = ("develop",)


def _resource_tree(root: Traversable, prefix: str = "") -> list[tuple[str, Traversable]]:
    out: list[tuple[str, resources.abc.Traversable]] = []
    for child in sorted(root.iterdir(), key=lambda node: node.name):
        rel = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_file():
            out.append((rel, child))
        else:
            out.extend(_resource_tree(child, rel))
    return out


def extract_line(name: str, dest: Path) -> list[Path]:
    """Write packaged reference-line wiring under *dest*; refuse when any path exists."""
    if name not in LINES:
        raise LookupError(name)
    files_root = resources.files(f"issuesmith.lines.{name}") / "files"
    entries = _resource_tree(files_root)
    conflicts: list[str] = []
    for rel, _node in entries:
        if (dest / rel).exists():
            conflicts.append(rel)
    if conflicts:
        msg = "existing files: " + ", ".join(sorted(conflicts))
        raise FileExistsError(msg)
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for rel, node in sorted(entries, key=lambda item: item[0]):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(node.read_bytes())
        written.append(target)
    return written
