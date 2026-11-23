"""README contract tests — README vs package source (#7850)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SRC = REPO_ROOT / "src" / "issuesmith"

PUBLIC_MODULES: tuple[tuple[str, Path], ...] = (
    ("issuesmith.contract", SRC / "contract.py"),
    ("issuesmith.verbs", SRC / "verbs" / "__init__.py"),
    ("issuesmith.gates", SRC / "gates" / "__init__.py"),
    ("issuesmith.config", SRC / "config.py"),
    ("issuesmith.andon", SRC / "andon.py"),
    ("issuesmith.queue", SRC / "queue.py"),
    ("issuesmith.forge", SRC / "forge.py"),
)

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
    "What it is not",
    "### Public API Stability",
    "When the line stops",
)

_ISSUESMITH_CONFIG_SKIP = frozenset({"root", "sections", "sub_design_subsections", "language"})

_ENV_PATTERNS = (
    re.compile(r"os\.getenv\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"os\.environ\.get\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]"),
    re.compile(r"os\.environ\[['\"]([A-Z][A-Z0-9_]+)['\"]\]"),
)

_SHIM_MARKERS = ("m2_gate", "cp1_gate", "b1_verify", ".m2", ".cp1", "b1_tier", "cp2_tier")
_LEGACY_GATE_IDS = frozenset(
    {"b1_ac_format", "b1_migration", "b1_milestone_subdesign"}
)


def _readme() -> str:
    return README_PATH.read_text(encoding="utf-8")


def _section(text: str, heading: str, until: str | None = None) -> str:
    start = text.find(heading)
    assert start >= 0, f"missing heading {heading!r}"
    rest = text[start + len(heading) :]
    if until:
        end = rest.find(until)
        if end >= 0:
            return rest[:end]
    return rest


def _project_version() -> str:
    return tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["version"]


def _cli_handlers() -> list[str]:
    tree = ast.parse((SRC / "cli.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == "_HANDLERS"
                    and isinstance(node.value, ast.Dict)
                ):
                    return [ast.literal_eval(key) for key in node.value.keys]
    raise AssertionError("_HANDLERS not found in cli.py")


def _module_all(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return list(ast.literal_eval(node.value))
    return []


def _architecture_paths() -> list[str]:
    paths: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(REPO_ROOT / "src").as_posix()
        segment = rel.split("/")[-1]
        if segment.startswith("_") and segment != "__init__.py":
            continue
        if any(part.startswith("_") for part in rel.split("/") if part != "__init__.py"):
            continue
        paths.append(rel)
    return paths


def _config_dataclass_fields() -> list[tuple[str, str]]:
    tree = ast.parse((SRC / "config.py").read_text(encoding="utf-8"))
    out: list[tuple[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Config"):
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                out.append((node.name, item.target.id))
    return out


def _literal_env_vars() -> set[str]:
    env: set[str] = set()
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _ENV_PATTERNS:
            env.update(pattern.findall(text))
    return env


def _public_exception_types() -> set[str]:
    types: set[str] = set()
    for path in SRC.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            for base in node.bases:
                base_name = ""
                if isinstance(base, ast.Name):
                    base_name = base.id
                elif isinstance(base, ast.Attribute):
                    base_name = base.attr
                if base_name.endswith(("Error", "Exception")) or base_name in {
                    "Exception",
                    "RuntimeError",
                    "ValueError",
                }:
                    types.add(node.name)
                    break
    return types


def _field_documented(config_section: str, field: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9_]){re.escape(field)}(?![a-z0-9_])", config_section))


def _cli_handler_documented(cli_section: str, handler: str) -> bool:
    return bool(re.search(rf"`{re.escape(handler)}(?:\s|<|`|$)", cli_section))


def _table_lines(section: str) -> list[str]:
    return [line for line in section.splitlines() if line.startswith("|") and "|---" not in line]


def _changelog_retired_symbols() -> set[str]:
    text = CHANGELOG_PATH.read_text(encoding="utf-8")
    symbols: set[str] = set()
    in_block = False
    for line in text.splitlines():
        if line.startswith("### Deprecated") or line.startswith("### Removed"):
            in_block = True
            continue
        if in_block and line.startswith("###"):
            in_block = False
        if not in_block:
            continue
        for token in re.findall(r"`([^`]+)`", line):
            if token.startswith("issuesmith.") and any(marker in token for marker in _SHIM_MARKERS):
                symbols.add(token)
            if re.fullmatch(r"[a-z0-9]+-[a-z0-9-]+", token) and (
                token.endswith("-gate") or token.endswith("-verify")
            ):
                symbols.add(token)
            if token == "recover" and "CLI" in line:
                symbols.add(token)
            if token in _LEGACY_GATE_IDS:
                symbols.add(token)
            if token in {"cp1", "m2"} and "gate" in line.lower():
                symbols.add(token)
            if token == "P0_FETCH_LOCK_WAIT":
                symbols.add(token)
    return symbols


def _markdown_links(text: str) -> list[str]:
    return re.findall(r"\[[^\]]*\]\(([^)]+)\)", text)


def test_readme_documents_required_sections_release_and_recovery():
    """Refs #7850 AC-1 — OSS sections, release metadata, and recovery operators."""
    readme = _readme()
    version = _project_version()
    for heading in _REQUIRED_SECTIONS:
        assert heading in readme, f"README missing {heading!r}"

    assert "Deprecated API" in readme or "extension points" in readme

    status = _section(readme, "## Status", "## Installation")
    assert f"v{version}" in status, "Status must name the pyproject.toml release"
    assert version in status
    assert "MIT" in status or "pre-1.0" in status

    quick = _section(readme, "## Quick Start", "## CLI Reference")
    assert "When the line stops" in quick
    assert "python3 -m issuesmith andon answer" in quick
    assert "resume" in quick

    for target in _markdown_links(readme):
        if target.startswith(("http://", "https://", "#")):
            continue
        path = target.split("#", 1)[0]
        if not path:
            continue
        resolved = (REPO_ROOT / path).resolve()
        assert resolved.exists(), f"broken README link: {target!r}"


def test_readme_cli_and_public_api_match_source():
    """Refs #7850 AC-2 — CLI handlers and public __all__ names appear in README tables."""
    readme = _readme()
    cli_section = _section(readme, "## CLI Reference", "## Output contract")
    api_section = _section(readme, "## Public API", "## Architecture")

    missing_handlers = [h for h in _cli_handlers() if not _cli_handler_documented(cli_section, h)]
    assert not missing_handlers, f"CLI handlers missing from README: {missing_handlers}"

    missing_exports: list[str] = []
    for mod, path in PUBLIC_MODULES:
        for name in _module_all(path):
            if f"`{name}`" not in api_section:
                missing_exports.append(f"{mod}.{name}")
    assert not missing_exports, f"public API names missing from README Public API table: {missing_exports}"


def test_readme_architecture_and_configuration_match_source():
    """Refs #7850 AC-3 — modules, config keys, and env vars documented in README."""
    readme = _readme()
    arch_section = _section(readme, "## Architecture", "## Gates")
    config_section = _section(readme, "## Configuration", "## Error Reference")

    missing_paths = [p for p in _architecture_paths() if f"`{p}`" not in arch_section]
    assert not missing_paths, f"source modules missing from Architecture: {missing_paths[:20]}"

    missing_fields: list[str] = []
    for cls, field in _config_dataclass_fields():
        if cls == "IssuesmithConfig" and field in _ISSUESMITH_CONFIG_SKIP:
            continue
        if not _field_documented(config_section, field):
            missing_fields.append(f"{cls}.{field}")
    if not _field_documented(config_section, "language_pack"):
        missing_fields.append("yaml.language_pack")
    assert not missing_fields, f"config fields missing from Configuration: {missing_fields}"

    missing_env = sorted(v for v in _literal_env_vars() if v not in config_section)
    assert not missing_env, f"environment variables missing from Configuration: {missing_env}"


def test_readme_error_reference_matches_public_exceptions():
    """Refs #7850 AC-4 — consumer-facing exception types appear in Error Reference."""
    readme = _readme()
    err_section = _section(readme, "## Error Reference", "## License")
    missing = sorted(t for t in _public_exception_types() if f"`{t}`" not in err_section)
    assert not missing, f"exception types missing from Error Reference: {missing}"


def test_readme_current_interfaces_match_changelog():
    """Refs #7850 AC-5 — retired symbols from CHANGELOG do not remain in CLI/API tables."""
    readme = _readme()
    cli_table = "\n".join(_table_lines(_section(readme, "## CLI Reference", "## Output contract")))
    api_table = "\n".join(
        _table_lines(_section(readme, "## Public API", "## Architecture"))
    )
    still_listed: list[str] = []
    for symbol in sorted(_changelog_retired_symbols()):
        if symbol == "recover":
            if re.search(rf"\| `{re.escape(symbol)}(?:\s|<|`|$)", cli_table):
                still_listed.append(symbol)
            continue
        if symbol in cli_table or symbol in api_table:
            still_listed.append(symbol)
    assert not still_listed, f"retired symbols still listed in CLI/Public API tables: {still_listed}"
