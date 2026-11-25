"""Pin version reading from pyproject.toml text."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from packaging.version import Version

if TYPE_CHECKING:
    from issuesmith.config import IssuesmithConfig

_URL_PIN_RE = re.compile(
    r"(?P<package>[a-zA-Z0-9_-]+)(?:\[[^\]]*\])?"
    r"\s*@\s*git\+[^@]+@v?(?P<version>\d+\.\d+\.\d+)"
)
_LOWER_BOUND_RE = re.compile(
    r"(?P<package>[A-Za-z0-9_.-]+)(?:\[[^\]]*\])?"
    r"\s*>=\s*v?(?P<version>\d+\.\d+\.\d+)"
)


def url_pin_version(pyproject_text: str, package: str) -> Version | None:
    """Read a git-pin version for ``package`` from ``pyproject.toml`` text."""
    for match in _URL_PIN_RE.finditer(pyproject_text):
        if match.group("package") == package:
            return Version(match.group("version"))
    return None


def lower_bound_version(pyproject_text: str, package: str) -> Version | None:
    """Read a ``>=`` lower-bound version for ``package`` from ``pyproject.toml`` text."""
    for match in _LOWER_BOUND_RE.finditer(pyproject_text):
        if match.group("package") == package:
            return Version(match.group("version"))
    return None


def base_pyproject_text(metadata: dict, config: IssuesmithConfig) -> str | None:
    """Return ``origin/<base_branch>:pyproject.toml`` from the scoped checkout, if readable."""
    from issuesmith.scope_gate import resolve_scope_root

    root = resolve_scope_root(metadata, config)
    if root is None:
        return None
    base_branch = str(metadata.get("base_branch") or "main")
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"origin/{base_branch}:pyproject.toml"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return proc.stdout
    return None
