"""tests/test_scope_gate_root.py — resolve_scope_root single-source-of-truth (#3487 AC-1).

CP1 (gate_rules.scope_breadth), gate-preflight, and P0 (steps.p0_worktree) must
all resolve the same measurement root for a given Issue metadata. Before this,
scope_breadth.py carried its own private ``_resolve_root`` copy that diverged
from what P0 actually measured (#3483: CP1 passed silently, P0 tripped on the
same allow_paths at 91 files / 21076 lines).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from issuesmith.gate_rules import scope_breadth
from issuesmith.steps import p0_worktree
from issuesmith.steps.scope_gate import resolve_scope_root


def _cfg(tmp_path, external_dir=None):
    cfg = MagicMock()
    cfg.root = tmp_path / "nexus"
    cfg.paths.external_dir = external_dir or (tmp_path / "nexus" / ".claude" / "external")
    return cfg


def test_no_target_repo_resolves_to_nexus_root(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({}, cfg) == cfg.root


def test_target_repo_is_nexus_resolves_to_nexus_root(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({"target_repo": "sumipan/nexus"}, cfg) == cfg.root


def test_cross_repo_resolves_to_external_dir_repo_when_clone_present(tmp_path):
    cfg = _cfg(tmp_path)
    (cfg.paths.external_dir / "issuesmith" / ".git").mkdir(parents=True)
    root = resolve_scope_root({"target_repo": "sumipan/issuesmith"}, cfg)
    assert root == cfg.paths.external_dir / "issuesmith"


def test_cross_repo_returns_none_when_clone_missing(tmp_path):
    """Missing clone must fail closed — never fall back to nexus root (#3483)."""
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({"target_repo": "sumipan/issuesmith"}, cfg) is None


def test_malformed_target_repo_returns_none(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({"target_repo": "not-a-valid-repo-slug"}, cfg) is None


def test_scope_breadth_gate_imports_the_shared_function():
    """scope_breadth.py must not carry a second, divergent root resolver (#3487 R1)."""
    assert scope_breadth.resolve_scope_root is resolve_scope_root
    assert not hasattr(scope_breadth, "_resolve_root")


def test_p0_worktree_imports_the_shared_function():
    """steps.p0_worktree must consult the same resolver CP1 uses (#3487 AC-1)."""
    assert p0_worktree.scope_gate_mod.resolve_scope_root is resolve_scope_root
