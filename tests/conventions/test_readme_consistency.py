"""README version strings and documented step modules stay aligned with the repo."""
from __future__ import annotations

import importlib.util
import re

from tests.conventions._util import REPO_ROOT

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
_GHDAG_GIT_V = re.compile(r"github\.com/sumipan/ghdag\.git@v([\d.]+(?:[a-zA-Z0-9._-]*)?)")
_PY_VERSION = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)
_GHDAG_PIN = re.compile(
    r"ghdag @ git\+https://github\.com/sumipan/ghdag\.git@v([\d.]+(?:[a-zA-Z0-9._-]*)?)"
)
_MODULE_LINE = re.compile(r"^\s*module:\s*([a-zA-Z_][\w.]*)\s*$", re.MULTILINE)


def _pyproject_version() -> str:
    text = _PYPROJECT.read_text(encoding="utf-8")
    m = _PY_VERSION.search(text)
    assert m, "pyproject.toml missing [project].version"
    return m.group(1)


def _pyproject_ghdag_pins() -> set[str]:
    text = _PYPROJECT.read_text(encoding="utf-8")
    return set(_GHDAG_PIN.findall(text))


def _issuesmith_versions_in_readme(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        if "since v" in line.lower():
            continue
        found.update(_ISSUESMITH_GIT_V.findall(line))
        found.update(_VERSION_BADGE.findall(line))
        found.update(_CURRENT_RELEASE.findall(line))
    return found


def _ghdag_versions_in_readme(text: str) -> set[str]:
    found: set[str] = set()
    for line in text.splitlines():
        if "since v" in line.lower():
            continue
        found.update(_GHDAG_GIT_V.findall(line))
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

    ghdag_in_readme = _ghdag_versions_in_readme(readme)
    if ghdag_in_readme:
        pins = _pyproject_ghdag_pins()
        assert pins, "README cites ghdag tags but pyproject has no ghdag pin"
        bad_gh = {v for v in ghdag_in_readme if v not in pins}
        assert not bad_gh, f"ghdag tags in README {bad_gh} not in pyproject pins {pins}"


def test_readme_step_modules_resolve():
    readme = _README.read_text(encoding="utf-8")
    unresolved = unresolved_issuesmith_step_modules(readme)
    assert not unresolved, f"README documents missing issuesmith step modules: {unresolved}"

    sample = "```yaml\nmodule: issuesmith.steps.implement\n```\n"
    assert "issuesmith.steps.implement" in unresolved_issuesmith_step_modules(sample)
