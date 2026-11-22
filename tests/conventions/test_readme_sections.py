"""OSS README §4.1 section order, License SPDX, and backtick module references."""
from __future__ import annotations

import importlib
import importlib.util
import re
import sys

from tests.conventions._util import REPO_ROOT

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pytest

_README = REPO_ROOT / "README.md"
_PYPROJECT = REPO_ROOT / "pyproject.toml"

_H2 = re.compile(r"^## (.+)$", re.MULTILINE)
_REQUIRED_HEADINGS = (
    "Status",
    "Installation",
    "Quick Start",
    "CLI Reference",
    "Output contract",
    "Public API",
    "Architecture",
    "Gates",
    "Configuration",
    "Error Reference",
    "License",
)

_SKIP_TAIL = frozenset({"yaml", "yml", "md", "py", "toml", "json"})
_BACKTICK = re.compile(r"`([^`\n]+)`")
_LICENSE_SECTION = re.compile(r"^## License\b", re.MULTILINE)
_SPDX_BACKTICK = re.compile(r"SPDX\s+`([^`]+)`", re.IGNORECASE)


def _h2_titles(text: str) -> list[str]:
    return _H2.findall(text)


def ordered_required_sections(text: str) -> list[str]:
    """Return required ``## `` titles in document order."""
    required = set(_REQUIRED_HEADINGS)
    return [title for title in _h2_titles(text) if title in required]


def _license_section(text: str) -> str:
    start = _LICENSE_SECTION.search(text)
    assert start is not None, "README missing ## License"
    return text[start.start() :]


def _normalize_issuesmith_ref(raw: str) -> str | None:
    raw = raw.strip()
    if not raw.startswith("issuesmith."):
        return None
    if "/" in raw or "*" in raw:
        return None
    if "(" in raw:
        raw = raw[: raw.index("(")]
    raw = raw.rstrip(".")
    if ":" in raw:
        mod, _, attr = raw.partition(":")
        if mod.count(".") >= 1 and attr:
            raw = f"{mod}.{attr}"
        else:
            raw = mod
    parts = raw.split(".")
    if parts[-1] in _SKIP_TAIL:
        return None
    return raw


def _resolve_dotted_ref(ref: str) -> str | None:
    """Return an error message when ``ref`` cannot be import/getattr-resolved."""
    parts = ref.split(".")
    last_err = ref
    for start in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:start])
        try:
            if importlib.util.find_spec(mod_name) is None:
                continue
            obj = importlib.import_module(mod_name)
            for attr in parts[start:]:
                obj = getattr(obj, attr)
            return None
        except Exception as exc:
            last_err = f"{ref} ({mod_name}: {type(exc).__name__}: {exc})"
    return last_err


def unresolved_issuesmith_backtick_refs(text: str) -> list[str]:
    """Backtick ``issuesmith.*`` spans that do not resolve to live objects."""
    errors: list[str] = []
    seen: set[str] = set()
    for match in _BACKTICK.finditer(text):
        ref = _normalize_issuesmith_ref(match.group(1))
        if ref is None or ref in seen:
            continue
        seen.add(ref)
        err = _resolve_dotted_ref(ref)
        if err:
            errors.append(err)
    return errors


@pytest.mark.readme_contract
def test_readme_has_required_sections():
    """Refs sumipan/nexus#7801 — OSS §4.1 headings exist in the required relative order."""
    text = _README.read_text(encoding="utf-8")
    assert ordered_required_sections(text) == list(_REQUIRED_HEADINGS)
    missing_license = text.replace("## License", "", 1)
    assert ordered_required_sections(missing_license) != list(_REQUIRED_HEADINGS)


@pytest.mark.readme_contract
def test_readme_license_matches_pyproject():
    """Refs sumipan/nexus#7801 — License section SPDX matches pyproject [project].license."""
    readme = _README.read_text(encoding="utf-8")
    section = _license_section(readme)
    match = _SPDX_BACKTICK.search(section)
    assert match, "README ## License must include SPDX `...`"
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    license_value = data.get("project", {}).get("license")
    assert license_value, "pyproject.toml missing [project].license"
    assert match.group(1) == license_value


@pytest.mark.readme_contract
def test_readme_issuesmith_references_resolve():
    """Refs sumipan/nexus#7801 — backtick issuesmith.* paths resolve via importlib/getattr."""
    readme = _README.read_text(encoding="utf-8")
    unresolved = unresolved_issuesmith_backtick_refs(readme)
    assert not unresolved, f"unresolved README issuesmith references: {unresolved}"
