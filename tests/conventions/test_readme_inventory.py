"""README must list every module, env var, exception and CLI command the package exposes."""
from __future__ import annotations

import re

from issuesmith import cli
from tests.conventions._util import REPO_ROOT, SRC, py_files

_README = REPO_ROOT / "README.md"
_EXCEPTION_RE = re.compile(
    r"^class ([A-Za-z][A-Za-z0-9_]*)\([^)]*(?:Error|Exception)",
    re.MULTILINE,
)
_ENV_GET = re.compile(
    r"""os\.(?:environ\.get|getenv)\(\s*["']([A-Z][A-Z0-9_]+)["']"""
)
_ENV_INDEX = re.compile(r"""os\.environ\[["']([A-Z][A-Z0-9_]+)["']\]""")
_ARCH_PATH = re.compile(r"`issuesmith/([^`]+\.py)`")
_BACKTICK_SPAN = re.compile(r"`([^`]+)`")
_CLI_LEAD = re.compile(r"^([a-z][a-z0-9-]*)(?:\s|<|$)")


def _section(text: str, heading: str) -> str:
    """Return body from ``heading`` until the next ``## `` heading."""
    lines: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith(heading):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            lines.append(line)
    return "\n".join(lines)


def _subsection(text: str, heading: str) -> str:
    """Return body from ``heading`` until the next ``### `` or ``## `` heading."""
    lines: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith(heading):
            in_section = True
            continue
        if in_section and (line.startswith("### ") or line.startswith("## ")):
            break
        if in_section:
            lines.append(line)
    return "\n".join(lines)


def _env_table_first_column(subsection: str) -> set[str]:
    names: set[str] = set()
    for line in subsection.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 3:
            continue
        name = cols[1].strip("`")
        if name and name != "Name":
            names.add(name)
    return names


def _error_table_first_column(section: str) -> set[str]:
    names: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cols = [c.strip() for c in line.split("|")]
        if len(cols) < 3:
            continue
        name = cols[1].strip("`")
        if name and name != "Type":
            names.add(name)
    return names


def src_module_paths() -> set[str]:
    base = SRC
    return {
        str(p.relative_to(base)).replace("\\", "/")
        for p in py_files(base)
    }


def src_env_vars() -> set[str]:
    names: set[str] = set()
    for path in py_files(SRC):
        text = path.read_text(encoding="utf-8")
        names.update(_ENV_GET.findall(text))
        names.update(_ENV_INDEX.findall(text))
    return names


def src_public_exceptions() -> set[str]:
    names: set[str] = set()
    for path in py_files(SRC):
        text = path.read_text(encoding="utf-8")
        for m in _EXCEPTION_RE.finditer(text):
            name = m.group(1)
            if not name.startswith("_"):
                names.add(name)
    return names


def test_readme_architecture_lists_every_module():
    """Every ``src/issuesmith`` module path appears under ``## Architecture``."""
    readme = _README.read_text(encoding="utf-8")
    arch = _section(readme, "## Architecture")
    documented = {m.group(1) for m in _ARCH_PATH.finditer(arch)}
    missing = sorted(src_module_paths() - documented)
    assert not missing, f"Architecture section missing module paths: {missing}"


def test_readme_configuration_lists_every_env_var():
    """Every env var read in ``src`` appears in ``### Environment variables``."""
    readme = _README.read_text(encoding="utf-8")
    env_sub = _subsection(readme, "### Environment variables")
    documented = _env_table_first_column(env_sub)
    missing = sorted(src_env_vars() - documented)
    assert not missing, f"Environment variables table missing: {missing}"


def test_readme_error_reference_lists_every_exception():
    """``## Error Reference`` types match public exception classes in ``src``."""
    readme = _README.read_text(encoding="utf-8")
    err_sec = _section(readme, "## Error Reference")
    documented = _error_table_first_column(err_sec)
    actual = src_public_exceptions()
    missing = sorted(actual - documented)
    extra = sorted(documented - actual)
    assert not missing and not extra, (
        f"Error Reference mismatch — missing {missing}, extra {extra}"
    )


def test_readme_cli_reference_lists_every_command():
    """``## CLI Reference`` backticks name every ``cli._HANDLERS`` key."""
    readme = _README.read_text(encoding="utf-8")
    cli_sec = _section(readme, "## CLI Reference")
    documented: set[str] = set()
    for m in _BACKTICK_SPAN.finditer(cli_sec):
        lead = _CLI_LEAD.match(m.group(1).strip())
        if lead:
            documented.add(lead.group(1))
    registered = set(cli._HANDLERS)
    missing = sorted(registered - documented)
    assert not missing, f"CLI Reference missing commands: {missing}"
