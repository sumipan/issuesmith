"""Public export contract for issuesmith.verbs and README surface (sumipan/nexus#6560)."""
from __future__ import annotations

import importlib
import re

import issuesmith.verbs as verbs_mod
from issuesmith import verbs as issuesmith_verbs
from issuesmith.engine import (
    exec_file as engine_exec_file,
)
from issuesmith.engine import (
    resolve as engine_resolve,
)
from issuesmith.engine import (
    route as engine_route,
)
from issuesmith.engine import (
    run_guarded as engine_run_guarded,
)
from issuesmith.engine import (
    run_verified as engine_run_verified,
)
from issuesmith.gates.worktree import line_has_cjk
from issuesmith.ops.dispatch import map_step_result as dispatch_map_step_result
from issuesmith.verbs import acceptance as acceptance_mod
from issuesmith.verbs import children as children_mod
from issuesmith.verbs import worktree as worktree_mod
from tests.conventions._util import REPO_ROOT

VERBS_PUBLIC_NAMES = frozenset(
    {
        "AcceptanceError",
        "AcceptanceVerdict",
        "ReviewFacts",
        "ChildPlan",
        "ChildPlanEntry",
        "ChildPlanError",
        "WorktreeError",
        "block_design_only_issue",
        "check_acceptance",
        "check_contract",
        "cleanup_branches",
        "cleanup_worktrees",
        "close_issue",
        "create_child",
        "ensure_base_included",
        "exec_file",
        "extract_contract",
        "find_pr",
        "map_step_result",
        "merge_pr",
        "merge_state",
        "plan_children",
        "prepare_worktree",
        "publish_branch",
        "resolve",
        "resolve_base_ref",
        "route",
        "review_tier",
        "run_guarded",
        "run_verified",
        "unchecked_acceptance_count",
        "validate_children",
        "widen_allow_paths",
    }
)

PUBLIC_MODULES = (
    "issuesmith.contract",
    "issuesmith.verbs",
    "issuesmith.gates",
    "issuesmith.config",
    "issuesmith.andon",
    "issuesmith.queue",
    "issuesmith.forge",
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
    """issuesmith.verbs __all__ matches the contract; re-exports share definition objects."""
    assert frozenset(verbs_mod.__all__) == VERBS_PUBLIC_NAMES
    for name in VERBS_PUBLIC_NAMES:
        assert hasattr(issuesmith_verbs, name), f"verbs missing attribute {name}"
    assert issuesmith_verbs.run_guarded is engine_run_guarded
    assert issuesmith_verbs.run_verified is engine_run_verified
    assert issuesmith_verbs.exec_file is engine_exec_file
    assert issuesmith_verbs.resolve is engine_resolve
    assert issuesmith_verbs.route is engine_route
    assert issuesmith_verbs.map_step_result is dispatch_map_step_result
    assert issuesmith_verbs.WorktreeError is worktree_mod.WorktreeError
    assert issuesmith_verbs.prepare_worktree is worktree_mod.prepare_worktree
    assert issuesmith_verbs.ensure_base_included is worktree_mod.ensure_base_included
    assert issuesmith_verbs.block_design_only_issue is worktree_mod.block_design_only_issue
    assert issuesmith_verbs.resolve_base_ref is worktree_mod.resolve_base_ref
    assert issuesmith_verbs.check_acceptance is acceptance_mod.check_acceptance
    assert issuesmith_verbs.AcceptanceError is acceptance_mod.AcceptanceError
    assert issuesmith_verbs.plan_children is children_mod.plan_children
    assert issuesmith_verbs.ChildPlanError is children_mod.ChildPlanError


def test_readme_public_modules_match_all():
    """README Public API table lists seven modules and each row matches that module __all__."""
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
