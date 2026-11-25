"""Reject feature Issues that change dependency pins in pyproject.toml."""

from __future__ import annotations

import re
from pathlib import Path

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import extract_change_table_rows
from issuesmith.gate_registry import GATE_REGISTRY, Violation
from issuesmith.pins import base_pyproject_text, lower_bound_version, url_pin_version
from issuesmith.pr_scope import normalize_allow_path

_VERSION_TOKEN = re.compile(r"v?\d+\.\d+\.\d+")
_IDEO_COMMA = bytes.fromhex("e38081").decode("utf-8")
_IDEO_PERIOD = bytes.fromhex("e38082").decode("utf-8")
_CLAUSE_DELIMS = _IDEO_COMMA + _IDEO_PERIOD + ",;|"
_CLAUSE_SPLIT = re.compile("[" + re.escape(_CLAUSE_DELIMS) + "]")
# A clause states a pin change only when it names the pin itself: the word
# "pin" (pin / pins / pinned) or a direct reference
# ("<pkg> @ git+...").  A dependency name next to a version number is not
# enough: designs cite the version they port from or verified against.
_PIN_WORD = re.compile(r"(?<![A-Za-z])pin(?:s|ned)?(?![A-Za-z])|@\s*git\+", re.IGNORECASE)

_REQUIRES_PINS_REMOVED_HINT = (
    "requires_pins was removed; open a pin-bump Issue for the dependency, "
    "list it under ## Dependencies as - #N, and remove requires_pins from the YAML."
)
_IN_FEATURE_FIX_HINT = (
    "Dependency pins are raised by the bump workflow; open a pin-bump Issue "
    "and list it under ## Dependencies as - #N."
)
_IN_FEATURE_FIX_HINT_KEEP_PYPROJECT = (
    "Dependency pins are raised by the bump workflow; open a pin-bump Issue "
    "and list it under ## Dependencies as - #N. "
    "Keep pyproject.toml in allow_paths: the change table edits it "
    "for another reason (for example the repo's own version)."
)


def _self_package(metadata: dict) -> str | None:
    target_repo = metadata.get("target_repo")
    if not target_repo:
        return None
    name = str(target_repo).rsplit("/", 1)[-1]
    if name in get_config().pin_packages:
        return name
    return None


def _has_pyproject_in_paths(metadata: dict) -> bool:
    for key in ("allow_paths", "host_allow_paths"):
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


def _is_pin_only_issue(body: str, target_repo: str) -> bool:
    """True when every target_repo change-table row is pyproject.toml or under tests/."""
    rows = extract_change_table_rows(body)
    target_rows = [
        (repo, path, change_type)
        for repo, path, change_type in rows
        if not repo.strip() or repo.strip() == target_repo
    ]
    if not target_rows:
        return False
    for _repo, path, _change_type in target_rows:
        try:
            normalized = normalize_allow_path(path)
        except ValueError:
            return False
        if normalized == "pyproject.toml":
            continue
        if normalized == "tests" or normalized.startswith("tests/"):
            continue
        return False
    return True


def _change_table_has_pyproject(body: str) -> bool:
    for _repo, path, _change_type in extract_change_table_rows(body):
        try:
            if normalize_allow_path(path) == "pyproject.toml":
                return True
        except ValueError:
            continue
    return False


def _pin_change_lines(body: str, package_names: frozenset[str]) -> list[str]:
    if not package_names:
        return []
    section = _design_section(body)
    hits: list[str] = []
    for line in section.splitlines():
        for raw_clause in _CLAUSE_SPLIT.split(line):
            clause = raw_clause.strip()
            if not clause:
                continue
            versions = _VERSION_TOKEN.findall(clause)
            if not versions:
                continue
            clause_hit = False
            for pkg in package_names:
                if pkg not in clause:
                    continue
                if not _PIN_WORD.search(clause):
                    continue
                clause_hit = True
                break
            if clause_hit:
                hits.append(clause)
    return hits


class PinBumpRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        try:
            metadata = parse_issue_metadata(body)
        except ValueError:
            return []

        if "requires_pins" in metadata:
            return [
                Violation(
                    rule_id="pin_bump.requires_pins_removed",
                    severity="fail",
                    message="feature Issue uses removed requires_pins metadata",
                    location=None,
                    auto_fixable=False,
                    fix_hint=_REQUIRES_PINS_REMOVED_HINT,
                )
            ]

        if any(lab.startswith("bump:") for lab in labels):
            return []

        target_repo = metadata.get("target_repo")
        if isinstance(target_repo, str) and _is_pin_only_issue(body, target_repo):
            return []

        if not _has_pyproject_in_paths(metadata):
            return []

        cfg = get_config()
        package_names = frozenset(cfg.pin_packages)
        self_package = _self_package(metadata)
        if self_package is not None:
            package_names = package_names - {self_package}
        base_text = base_pyproject_text(metadata, cfg)
        if base_text is not None:
            package_names = frozenset(
                pkg
                for pkg in package_names
                if not (
                    url_pin_version(base_text, pkg) is None
                    and lower_bound_version(base_text, pkg) is not None
                )
            )
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
                    _IN_FEATURE_FIX_HINT_KEEP_PYPROJECT
                    if _change_table_has_pyproject(body)
                    else _IN_FEATURE_FIX_HINT
                ),
            )
        ]


GATE_REGISTRY["pin_bump"] = PinBumpRules
