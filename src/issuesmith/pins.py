"""Pin version reading and comparison for requires_pins landing checks."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from packaging.version import InvalidVersion, Version

_PIN_RE = re.compile(
    r"(?P<package>[a-zA-Z0-9_-]+)(?:\[[^\]]*\])?"
    r"\s*@\s*git\+[^@]+@v?(?P<version>\d+\.\d+\.\d+)"
)


def requires_pins(metadata: dict) -> dict[str, Version]:
    """Return ``{package: Version}`` from issue metadata ``requires_pins``.

    Missing key returns ``{}``. Invalid shape or non-SemVer values raise ``ValueError``.
    """
    raw = metadata.get("requires_pins")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("requires_pins must be a mapping")
    out: dict[str, Version] = {}
    for key, value in raw.items():
        pkg = str(key).strip()
        if not pkg:
            raise ValueError("requires_pins keys must be non-empty strings")
        ver_str = str(value).strip()
        if ver_str.startswith("v"):
            ver_str = ver_str[1:]
        try:
            out[pkg] = Version(ver_str)
        except InvalidVersion as exc:
            raise ValueError(f"requires_pins[{pkg!r}]: invalid version {value!r}") from exc
    return out


def pin_version(pyproject_text: str, package: str) -> Version | None:
    """Read a git-pin version for ``package`` from ``pyproject.toml`` text."""
    for match in _PIN_RE.finditer(pyproject_text):
        if match.group("package") == package:
            return Version(match.group("version"))
    return None


def installed_version(install_dir: Path) -> Version | None:
    """Return the latest tag version at ``install_dir`` via ``git describe``."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(install_dir), "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    tag = proc.stdout.strip()
    if not tag:
        return None
    if tag.startswith("v"):
        tag = tag[1:]
    try:
        return Version(tag)
    except InvalidVersion:
        return None


def _fmt_version(ver: Version | None) -> str:
    return "unknown" if ver is None else f"v{ver}"


def unlanded_pins(
    required: dict[str, Version],
    base_text: str | None,
    installs: Mapping[str, Path],
) -> list[str]:
    """Return human-readable descriptions of pins not yet landed (empty when all ok)."""
    if not required:
        return []
    unlanded: list[str] = []
    for pkg, req_ver in sorted(required.items()):
        if base_text is None:
            base_ver: Version | None = None
        else:
            base_ver = pin_version(base_text, pkg)
        install_path = installs.get(pkg)
        if install_path is not None:
            inst_ver = installed_version(install_path)
        else:
            inst_ver = None

        pin_ok = base_ver is not None and base_ver >= req_ver
        if install_path is not None:
            install_ok = inst_ver is not None and inst_ver >= req_ver
            if pin_ok and install_ok:
                continue
        elif pin_ok:
            continue

        unlanded.append(
            f"{pkg} v{req_ver} (base={_fmt_version(base_ver)}, "
            f"installed={_fmt_version(inst_ver)})"
        )
    return unlanded
