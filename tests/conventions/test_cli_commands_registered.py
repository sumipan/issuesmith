"""Every command the docs, help text, or messages point at must exist in the CLI tables."""
from __future__ import annotations

import ast
import importlib.util
import re

import pytest

from issuesmith import cli
from tests.conventions._util import REPO_ROOT, SRC, mentioned_commands, py_files

# Sub-command groups dispatched by __main__ before cli._HANDLERS.
_MAIN_GROUPS = {"andon", "labels"}
# Words that appear after `issuesmith` in prose but are not commands.
_NOT_COMMANDS = {"pipeline", "package", "queue-tick"}


def _registered() -> set[str]:
    return set(cli._HANDLERS) | _MAIN_GROUPS


def test_every_handler_is_documented_in_usage():
    usage = cli._USAGE
    missing = [cmd for cmd in cli._HANDLERS if cmd not in usage]
    assert not missing, f"commands missing from _USAGE: {missing}"


def _usage_command_heads(usage: str) -> set[str]:
    """Command names named by _USAGE: the first token of each line plus single-word segments
    separated by two or more spaces (e.g. ``queue  deps  tier``)."""
    heads: set[str] = set()
    for raw in usage.splitlines():
        line = raw.strip()
        if not line or line.startswith(("usage:", "commands:")):
            continue
        segments = [seg.strip() for seg in re.split(r"\s{2,}", line) if seg.strip()]
        first = segments[0].split()[0]
        heads.update(first.split("|"))
        for seg in segments[1:]:
            if " " not in seg:
                heads.add(seg)
    return {h for h in heads if h and h[0].islower()}


def unknown_usage_commands(usage: str, registered: set[str]) -> set[str]:
    return _usage_command_heads(usage) - registered - _NOT_COMMANDS


def test_usage_lists_only_registered_commands():
    unknown = unknown_usage_commands(cli._USAGE, _registered())
    assert not unknown, f"_USAGE names unknown commands: {sorted(unknown)}"


def test_detects_command_dropped_from_table():
    """Reproduction of sumipan/nexus#3566: `resume` documented but not registered."""
    assert unknown_usage_commands(cli._USAGE, _registered() - {"resume"}) == {"resume"}


def unknown_mentioned_commands(registered: set[str]) -> dict[str, set[str]]:
    texts = [(REPO_ROOT / "README.md"), (REPO_ROOT / "CHANGELOG.md"), *py_files(SRC)]
    unknown: dict[str, set[str]] = {}
    for path in texts:
        for cmd in mentioned_commands(path.read_text(encoding="utf-8")):
            if cmd not in registered and cmd not in _NOT_COMMANDS:
                unknown.setdefault(cmd, set()).add(str(path.relative_to(REPO_ROOT)))
    return unknown


def test_commands_mentioned_in_docs_and_source_are_registered():
    unknown = unknown_mentioned_commands(_registered())
    assert not unknown, f"documented commands that do not exist: {unknown}"


def test_extractor_reads_both_invocation_forms():
    text = "run `python3 -m issuesmith resume 1 --from p0` or `issuesmith labels reconcile`"
    assert mentioned_commands(text) == {"resume", "labels"}


def test_detects_documented_command_dropped_from_table():
    """Every command the docs currently name becomes a violation once dropped from the table."""
    documented = set().union(*(mentioned_commands(p.read_text(encoding="utf-8")) for p in [REPO_ROOT / "README.md"]))
    documented &= set(cli._HANDLERS)
    assert documented, "README.md names no CLI command in backticks"
    dropped = sorted(documented)[0]
    assert dropped in unknown_mentioned_commands(_registered() - {dropped})


# --- every module a CLI handler imports lazily must exist in this source tree ---

# Handler imports that point at removed modules. Shrink; never grow.
_KNOWN_MISSING_HANDLER_IMPORTS = {
    "issuesmith.ops.label_hygiene": "sumipan/nexus#3612: cli labels handler imports a module removed in #3508",
}


def _handler_imports() -> list[str]:
    tree = ast.parse((SRC / "cli.py").read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("issuesmith."):
            modules.add(node.module)
    return sorted(modules)


def _handler_import_params():
    for mod in _handler_imports():
        reason = _KNOWN_MISSING_HANDLER_IMPORTS.get(mod)
        if reason:
            yield pytest.param(mod, marks=pytest.mark.xfail(strict=True, reason=reason))
        else:
            yield mod


@pytest.mark.parametrize("module", list(_handler_import_params()))
def test_handler_imports_resolve(module: str):
    assert importlib.util.find_spec(module) is not None, f"cli handler imports missing module {module}"
