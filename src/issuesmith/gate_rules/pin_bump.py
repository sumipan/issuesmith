"""Reject feature Issues that change dependency pins in pyproject.toml."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import extract_change_table_rows
from issuesmith.pr_scope import normalize_allow_path

_VERSION_TOKEN = re.compile(r"v?\d+\.\d+\.\d+")
_IDEO_COMMA = bytes.fromhex("e38081").decode("utf-8")
_IDEO_PERIOD = bytes.fromhex("e38082").decode("utf-8")
_CLAUSE_DELIMS = _IDEO_COMMA + _IDEO_PERIOD + ",;|"
_CLAUSE_SPLIT = re.compile("[" + re.escape(_CLAUSE_DELIMS) + "]")
# A clause states a pin change only when it names the pin itself: the word
# "pin" (pin / pins / pinned / requires_pins) or a direct reference
# ("<pkg> @ git+...").  A dependency name next to a version number is not
# enough: designs cite the version they port from or verified against.
_PIN_WORD = re.compile(r"(?<![A-Za-z])pin(?:s|ned)?(?![A-Za-z])|@\s*git\+", re.IGNORECASE)

_SELF_PIN_NOTE = (
    " Do not put the target_repo's own package in requires_pins"
    " (pins_landed reads dependency pins from that repo's pyproject.toml;"
    " a self pin never lands)."
)
_IN_FEATURE_FIX_HINT = (
    "pins are raised by the bump workflow; put the required version"
    " in requires_pins and remove pyproject.toml from allow_paths."
    + _SELF_PIN_NOTE
)
_IN_FEATURE_FIX_HINT_KEEP_PYPROJECT = (
    "pins are raised by the bump workflow; put the required version"
    " in requires_pins and drop the pin change from the design."
    " Keep pyproject.toml in allow_paths: the change table edits it"
    " for another reason (for example the repo's own version)."
    + _SELF_PIN_NOTE
)


def _self_package(metadata: dict) -> str | None:
    target_repo = metadata.get("target_repo")
    if not target_repo:
        return None
    name = str(target_repo).rsplit("/", 1)[-1]
    if name in get_config().installs:
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


def _normalize_pin_version(raw: str) -> str:
    value = raw.strip()
    if value[:1].lower() == "v":
        value = value[1:]
    return value


def _pin_change_lines(
    body: str,
    package_names: frozenset[str],
    required_pins: Mapping[str, object],
) -> list[str]:
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
                required = required_pins.get(pkg)
                if required is not None:
                    req_norm = _normalize_pin_version(str(required))
                    if all(_normalize_pin_version(v) == req_norm for v in versions):
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
        if any(lab.startswith("bump:") for lab in labels):
            return []
        try:
            metadata = parse_issue_metadata(body)
        except ValueError:
            return []

        self_package = _self_package(metadata)
        required = metadata.get("requires_pins") or {}
        if (
            isinstance(required, dict)
            and self_package is not None
            and self_package in required
        ):
            return [
                Violation(
                    rule_id="pin_bump.self_requires_pin",
                    severity="fail",
                    message=(
                        f"feature Issue lists {self_package} in requires_pins"
                        " for the same package as target_repo"
                    ),
                    location=None,
                    auto_fixable=False,
                    fix_hint=(
                        f"remove {self_package} from requires_pins;"
                        " pins_landed reads dependency git pins from"
                        " target_repo's pyproject.toml, so a pin on the"
                        " repo's own package cannot land"
                    ),
                )
            ]

        target_repo = metadata.get("target_repo")
        if isinstance(target_repo, str) and _is_pin_only_issue(body, target_repo):
            return []

        if not _has_pyproject_in_paths(metadata):
            return []

        cfg = get_config()
        if isinstance(required, dict):
            pkg_names = frozenset(str(k) for k in required)
        else:
            pkg_names = frozenset()
        package_names = frozenset(cfg.installs) | pkg_names
        if self_package is not None:
            package_names = package_names - {self_package}
        required_pins = required if isinstance(required, dict) else {}
        hits = _pin_change_lines(body, package_names, required_pins)
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
