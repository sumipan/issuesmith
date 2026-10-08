"""Reject feature Issues that change dependency pins in pyproject.toml."""

from __future__ import annotations

import re
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata

_VERSION_TOKEN = re.compile(r"v?\d+\.\d+\.\d+")


def _has_pyproject_in_paths(metadata: dict) -> bool:
    for key in ("allow_paths", "diary_allow_paths"):
        raw = metadata.get(key)
        if raw is None:
            continue
        paths = raw if isinstance(raw, list) else [raw]
        for path in paths:
            if Path(str(path)).name == "pyproject.toml":
                return True
    return False


def _design_section(body: str) -> str:
    sections = get_config().sections
    design_heading = sections.get("design", "Design")
    match = re.search(
        rf"^##\s+{re.escape(design_heading)}\s*\n(.*?)(?=^##\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else body


def _pin_change_lines(body: str, package_names: frozenset[str]) -> list[str]:
    if not package_names:
        return []
    section = _design_section(body)
    hits: list[str] = []
    for line in section.splitlines():
        if not _VERSION_TOKEN.search(line):
            continue
        for pkg in package_names:
            if pkg in line:
                hits.append(line)
                break
    return hits


class PinBumpRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if any(lab.startswith("bump:") for lab in labels):
            return []
        try:
            metadata = parse_issue_metadata(body)
        except ValueError:
            return []
        if not _has_pyproject_in_paths(metadata):
            return []

        cfg = get_config()
        required = metadata.get("requires_pins") or {}
        if isinstance(required, dict):
            pkg_names = frozenset(str(k) for k in required)
        else:
            pkg_names = frozenset()
        package_names = frozenset(cfg.installs) | pkg_names
        hits = _pin_change_lines(body, package_names)
        if not hits:
            return []

        pkg = next(
            (p for p in sorted(package_names) if p in hits[0]),
            sorted(package_names)[0] if package_names else "dependency",
        )
        return [
            Violation(
                rule_id="pin_bump.in_feature_issue",
                severity="fail",
                message=f"feature Issue changes the {pkg} pin in pyproject.toml",
                location=None,
                auto_fixable=False,
                fix_hint=(
                    "pins are raised by the bump workflow; put the required version"
                    " in requires_pins and remove pyproject.toml from allow_paths"
                ),
            )
        ]


GATE_REGISTRY["pin_bump"] = PinBumpRules
