"""README version strings and documented step modules stay aligned with the repo."""
from __future__ import annotations

import importlib.util
import re
import sys

from tests.conventions._util import REPO_ROOT

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

_README = REPO_ROOT / "README.md"
_PYPROJECT = REPO_ROOT / "pyproject.toml"

_ISSUESMITH_GIT_V = re.compile(
    r"github\.com/sumipan/issuesmith\.git@v([\d.]+(?:[a-zA-Z0-9._-]*)?)"
)
_VERSION_BADGE = re.compile(r"badge/version-v([\d.]+(?:[a-zA-Z0-9._-]*)?)-")
_CURRENT_RELEASE = re.compile(
    r"current release is \*\*v([\d.]+(?:[a-zA-Z0-9._-]*)?)\*\*",
    re.IGNORECASE,
)
_PY_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_MODULE_LINE = re.compile(r"^\s*module:\s*([a-zA-Z_][\w.]*)\s*$", re.MULTILINE)


def _pyproject_version() -> str:
    text = _PYPROJECT.read_text(encoding="utf-8")
    m = _PY_VERSION.search(text)
    assert m, "pyproject.toml missing [project].version"
    return m.group(1)


def _issuesmith_versions_in_readme(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        if "since v" in line.lower():
            continue
        found.update(_ISSUESMITH_GIT_V.findall(line))
        found.update(_VERSION_BADGE.findall(line))
        found.update(_CURRENT_RELEASE.findall(line))
    return found


def _modules_in_yaml_blocks(text: str) -> list[str]:
    modules: list[str] = []
    in_fence = False
    fence_lang = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                fence_lang = stripped[3:].strip().lower()
            else:
                in_fence = False
                fence_lang = ""
            continue
        if in_fence and fence_lang == "yaml":
            m = _MODULE_LINE.match(line)
            if m:
                modules.append(m.group(1))
    return modules


def unresolved_issuesmith_step_modules(text: str) -> list[str]:
    """Return documented `issuesmith.*` step modules that cannot be imported."""
    bad: list[str] = []
    for mod in _modules_in_yaml_blocks(text):
        if not mod.startswith("issuesmith."):
            continue
        try:
            ok = importlib.util.find_spec(mod) is not None
        except ModuleNotFoundError:
            ok = False
        if not ok:
            bad.append(mod)
    return bad


def test_readme_versions_match_pyproject():
    readme = _README.read_text(encoding="utf-8")
    expected = _pyproject_version()
    iss_versions = _issuesmith_versions_in_readme(readme)
    assert iss_versions, "README should mention the current issuesmith version at least once"
    bad_iss = {v for v in iss_versions if v != expected}
    assert not bad_iss, f"issuesmith versions in README {bad_iss} != pyproject {expected}"


def test_pyproject_ghdag_is_lower_bound_in_both_extras():
    """Refs sumipan/issuesmith#7744: ghdag extra uses the same lower-bound specifier."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]
    ghdag_spec = extras["ghdag"][0]
    dev_specs = [s for s in extras["dev"] if str(s).startswith("ghdag")]
    assert len(dev_specs) == 1
    dev_spec = dev_specs[0]
    assert ghdag_spec == dev_spec
    for spec in (ghdag_spec, dev_spec):
        assert spec.startswith("ghdag>=")
        assert "@" not in spec


_REQUIRED_SECTIONS = (
    "## Status",
    "## Installation",
    "## Quick Start",
    "## CLI Reference",
    "## Public API",
    "## Architecture",
    "## Configuration",
    "## Error Reference",
    "## License",
)

_SPDX_IN_LICENSE = re.compile(
    r"## License\b[\s\S]*?SPDX\s+`([^`]+)`",
    re.IGNORECASE,
)


def test_readme_has_required_sections():
    """OSS README §4.1 headings are present and License SPDX matches pyproject."""
    readme = _README.read_text(encoding="utf-8")
    for heading in _REQUIRED_SECTIONS:
        assert heading in readme, f"README missing section {heading}"
    m = _SPDX_IN_LICENSE.search(readme)
    assert m, "README ## License must include SPDX `...`"
    pyproject = _PYPROJECT.read_text(encoding="utf-8")
    license_m = re.search(r'^license\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert license_m, "pyproject.toml missing license"
    assert m.group(1) == license_m.group(1)


def test_readme_step_modules_resolve():
    readme = _README.read_text(encoding="utf-8")
    unresolved = unresolved_issuesmith_step_modules(readme)
    assert not unresolved, f"README documents missing issuesmith step modules: {unresolved}"

    sample = "```yaml\nmodule: issuesmith.steps.implement\n```\n"
    assert "issuesmith.steps.implement" in unresolved_issuesmith_step_modules(sample)
