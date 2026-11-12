"""Public export contract for issuesmith.verbs and README surface (sumipan/nexus#6560)."""
from __future__ import annotations

import importlib
import re

import issuesmith.verbs as verbs_mod
from issuesmith import verbs as issuesmith_verbs
from issuesmith.engine import (
    ROLE_ENGINES as engine_role_engines,
)
from issuesmith.engine import (
    exec_file as engine_exec_file,
)
from issuesmith.engine import (
    resolve as engine_resolve,
)
from issuesmith.engine import (
    run_guarded as engine_run_guarded,
)
from issuesmith.engine import (
    run_verified as engine_run_verified,
)
from issuesmith.gates.worktree import line_has_cjk
from issuesmith.ops.dispatch import map_step_result as dispatch_map_step_result
from tests.conventions._util import REPO_ROOT

VERBS_PUBLIC_NAMES = frozenset(
    {
        "prepare_worktree",
        "publish_branch",
        "find_pr",
        "merge_state",
        "merge_pr",
        "cleanup_worktrees",
        "cleanup_branches",
        "close_issue",
        "run_guarded",
        "run_verified",
        "exec_file",
        "resolve",
        "ROLE_ENGINES",
        "map_step_result",
    }
)

PUBLIC_MODULES = (
    "issuesmith.contract",
    "issuesmith.verbs",
    "issuesmith.gates",
    "issuesmith.config",
    "issuesmith.andon",
    "issuesmith.queue",
)

_MODULE_ROW_RE = re.compile(r"^\| `(issuesmith\.[^`]+)` \| (.+) \|$")
_SYMBOL_RE = re.compile(r"`([^`]+)`")


def _module_all(modpath: str) -> frozenset[str]:
    mod = importlib.import_module(modpath)
    return frozenset(mod.__all__)


def _parse_readme_public_api_table(readme_text: str) -> dict[str, frozenset[str]]:
    start = readme_text.find("## Public API")
    end = readme_text.find("### Public API Stability")
    if start < 0 or end < 0 or end <= start:
        msg = "README missing ## Public API or ### Public API Stability"
        raise AssertionError(msg)
    section = readme_text[start:end]
    table: dict[str, frozenset[str]] = {}
    for line in section.splitlines():
        match = _MODULE_ROW_RE.match(line.strip())
        if not match:
            continue
        modpath = match.group(1)
        assert modpath not in table, f"duplicate README Public API row: {modpath}"
        symbols = frozenset(_SYMBOL_RE.findall(match.group(2)))
        table[modpath] = symbols
    return table


def test_verbs_public_exports():
    """issuesmith.verbs __all__ matches the contract; six engine/dispatch names re-export."""
    assert frozenset(verbs_mod.__all__) == VERBS_PUBLIC_NAMES
    for name in VERBS_PUBLIC_NAMES:
        assert hasattr(issuesmith_verbs, name), f"verbs missing attribute {name}"
    assert issuesmith_verbs.run_guarded is engine_run_guarded
    assert issuesmith_verbs.run_verified is engine_run_verified
    assert issuesmith_verbs.exec_file is engine_exec_file
    assert issuesmith_verbs.resolve is engine_resolve
    assert issuesmith_verbs.ROLE_ENGINES is engine_role_engines
    assert issuesmith_verbs.map_step_result is dispatch_map_step_result


def test_readme_public_modules_match_all():
    """README Public API table lists six modules and each row matches that module __all__."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    table = _parse_readme_public_api_table(readme)
    assert frozenset(table) == frozenset(PUBLIC_MODULES)
    for modpath in PUBLIC_MODULES:
        assert table[modpath] == _module_all(modpath), modpath


def test_public_api_docs_are_english():
    """README and CHANGELOG Unreleased docs for the public API contain no CJK."""
    for rel in ("README.md", "CHANGELOG.md"):
        path = REPO_ROOT / rel
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not line_has_cjk(line), f"{rel}:{line_no}: CJK in public API docs"
