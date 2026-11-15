"""Unit tests for ScopeCouplingRules (CP1/B1 scope coupling gate, #3520).

Fixture bodies are ASCII-only; section names are configured to English via the
ascii_sections_config fixture where needed.
"""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules.scope_coupling import ScopeCouplingRules


@pytest.fixture(autouse=True)
def _clear_config_cache(monkeypatch):
    monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture()
def ascii_sections_config(tmp_path, monkeypatch):
    """Config with ASCII section names for test fixtures."""
    from issuesmith.config import load_config

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "sumipan/issuesmith",
            "sections": {
                "acceptance_criteria": "Acceptance Criteria",
                "changed_files": "Changed Files",
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    return load_config()


_FAKE_ROOT = Path("/fake/root")

# AC-1 fixture: run_guarded is modified via backtick identifier;
# allow_paths is missing steps/m2_finalize.py
_AC1_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/issuesmith/engine.py\n"
    "```\n\n"
    "## Design\n\n"
    "`run_guarded` adds validation to prevent invalid args.\n"
)

# AC-2 fixture: 4 live.md files listed in paths_must_not_exist;
# allow_paths missing 4 test files.
# Uses Acceptance Criteria section in English (requires ascii_sections_config).
_AC2_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - workflows/issuesmith/p0-setup.md\n"
    "```\n\n"
    "## Acceptance Criteria\n\n"
    "```yaml\n"
    "paths_must_not_exist:\n"
    "  - workflows/issuesmith/p2-recover-live.md\n"
    "  - workflows/issuesmith/p1-design-live.md\n"
    "  - workflows/issuesmith/p0-setup-live.md\n"
    "  - workflows/issuesmith/p2-impl-live.md\n"
    "```\n"
)

# AC-3 small: 1 allow_paths entry; room to widen
_AC3_BODY_SMALL = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/issuesmith/engine.py\n"
    "```\n\n"
    "## Design\n\n"
    "`run_guarded` is changed.\n"
)


def _make_grep_mock_ac1():
    """_git_grep side_effect: run_guarded defined in engine.py, used in m2_finalize."""
    def fake_grep(root, pattern, pathspec):
        if pattern == "def run_guarded":
            return ["src/issuesmith/engine.py"]
        if pattern == "class run_guarded":
            return []
        if pattern == "run_guarded" and pathspec == "src":
            return ["src/issuesmith/engine.py", "src/issuesmith/steps/m2_finalize.py"]
        if pattern == "run_guarded" and pathspec == "tests":
            return []
        return []
    return fake_grep


def _make_grep_mock_ac2():
    """_git_grep side_effect: deleted live.md basenames appear in 4 test files."""
    _LIVE_BASENAMES = {"p2-recover-live", "p1-design-live", "p0-setup-live", "p2-impl-live"}
    _TEST_FILES = [
        "tests/workflows/issuesmith/test_live_dispatch_sync.py",
        "tests/workflows/issuesmith/test_live_p0_setup.py",
        "tests/workflows/issuesmith/test_live_p1_design.py",
        "tests/workflows/issuesmith/test_live_p2_recover.py",
    ]

    def fake_grep(root, pattern, pathspec):
        # Basenames of paths_must_not_exist files match in tests
        if any(bn in pattern for bn in _LIVE_BASENAMES) or "p0-setup" in pattern:
            if pathspec == "tests":
                return _TEST_FILES
        return []
    return fake_grep


def _make_grep_mock_run_guarded(
    missing_src="src/issuesmith/steps/m2_finalize.py",
):
    """_git_grep side_effect: run_guarded defined in engine.py, found in missing_src."""
    def fake_grep(root, pattern, pathspec):
        if pattern == "def run_guarded":
            return ["src/issuesmith/engine.py"]
        if pattern == "class run_guarded":
            return []
        if pattern == "run_guarded" and pathspec == "src":
            return ["src/issuesmith/engine.py", missing_src]
        return []
    return fake_grep


class TestAC2TestsOutsideAllowPaths:
    """AC-2: live.md files deleted; 4 test files missing from allow_paths."""

    def test_returns_tests_violation_with_all_four_files(self, ascii_sections_config):
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_ac2(),
            ):
                violations = ScopeCouplingRules().check(_AC2_BODY, [])

        test_v = [v for v in violations if v.rule_id == "scope_coupling.tests_outside_allow_paths"]
        assert test_v, f"Expected tests violation, got: {violations}"
        hint = test_v[0].fix_hint
        assert test_v[0].severity == "fail"
        assert "test_live_dispatch_sync.py" in hint
        assert "test_live_p0_setup.py" in hint
        assert "test_live_p1_design.py" in hint
        assert "test_live_p2_recover.py" in hint


class TestAC3AutoWiden:
    """AC-3: auto-widen adds missing files when under max_files; blocked when over."""


    def test_widen_blocked_when_over_max_files(self):
        # 80 allow_paths + 1 missing = 81 > max_files(80)
        body_over = (
            "```yaml\n"
            "target_repo: sumipan/issuesmith\n"
            "base_branch: main\n"
            "allow_paths:\n"
            "  - src/issuesmith/engine.py\n"
            + "".join(f"  - src/issuesmith/file{i}.py\n" for i in range(79))
            + "```\n\n"
            "## Design\n\n"
            "`run_guarded` is changed.\n"
        )
        rule = ScopeCouplingRules()
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_run_guarded(),
            ):
                violations = rule.check(body_over, [])

        assert violations
        assert all(not v.auto_fixable for v in violations)
        assert rule.autofix_new_allow_paths is None
        # sumipan/nexus#3527 AC-3: the contradiction with scope_breadth is visible in the message
        assert all("scope_breadth max_files=80" in v.message for v in violations)


class TestAC5RegistryAndGateIntegration:
    """AC-5: GATE_REGISTRY contains scope_coupling; design_lint_entry aggregates violations."""

    def test_gate_registry_contains_scope_coupling(self):
        import issuesmith.gate_rules  # noqa: F401 -- ensures registration
        from issuesmith.gate_registry import GATE_REGISTRY

        assert "scope_coupling" in GATE_REGISTRY
        assert GATE_REGISTRY["scope_coupling"] is ScopeCouplingRules


    def test_design_lint_entry_sets_autofix_when_coupling_widens(self):
        from issuesmith.design_lint_entry import check_gate

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_run_guarded(),
            ):
                result = check_gate(_AC3_BODY_SMALL, [])

        assert result["autofix_new_allow_paths"] is not None
        assert "src/issuesmith/steps/m2_finalize.py" in result["autofix_new_allow_paths"]


class TestEdgeCases:
    """Edge cases: no allow_paths, no clone, no keys, invalid body."""


    def test_no_clone_returns_root_unavailable(self):
        """AC-5: clone absent -> scope_coupling.root_unavailable (fail), not empty."""
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=None,
        ):
            violations = ScopeCouplingRules().check(_AC1_BODY, [])
        assert len(violations) == 1
        v = violations[0]
        assert v.rule_id == "scope_coupling.root_unavailable"
        assert v.severity == "fail"
        assert v.auto_fixable is False


    def test_invalid_body_returns_empty(self):
        violations = ScopeCouplingRules().check("not a yaml block", [])
        assert violations == []


# ---------------------------------------------------------------------------
# sumipan/nexus#3527 — required vs reference keys, scope_mode: internal
# ---------------------------------------------------------------------------

_3431_BODY = (
    "```yaml\n"
    "target_repo: sumipan/ghdag\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/ghdag/workflow/dispatcher.py\n"
    "  - src/ghdag/workflow/loader.py\n"
    "{scope_mode}"
    "```\n\n"
    "## Design\n\n"
    "`WorkflowDispatcher` reloads definitions; `load_workflows` is called per poll.\n"
    "The `dispatcher` module keeps its public interface.\n"
)


def _make_grep_mock_3431():
    """load_workflows is defined in loader.py (changed) and used by 3 callers; the
    'dispatcher' basename matches 40 files by string only."""
    callers = [f"src/ghdag/cli/commands/{n}.py" for n in ("watch", "run", "status")]
    noise = [f"src/ghdag/x/mod{i}.py" for i in range(40)]

    def fake_grep(root, pattern, pathspec):
        if pattern == "def load_workflows":
            return ["src/ghdag/workflow/loader.py"]
        if pattern == "class WorkflowDispatcher":
            return ["src/ghdag/workflow/dispatcher.py"]
        if pattern.startswith("def ") or pattern.startswith("class "):
            return []
        if pattern == "load_workflows" and pathspec == "src":
            return ["src/ghdag/workflow/loader.py", *callers]
        if pattern == "WorkflowDispatcher" and pathspec == "src":
            return ["src/ghdag/workflow/dispatcher.py"]
        if pattern == "dispatcher" and pathspec == "src":
            return noise
        return []

    return fake_grep


class TestScopeModeInternal:
    def test_internal_mode_returns_no_violations(self):
        """AC-1: scope_mode: internal declares the public interface unchanged."""
        rule = ScopeCouplingRules()
        body = _3431_BODY.format(scope_mode="scope_mode: internal\n")
        with mock.patch("issuesmith.gate_rules.scope_coupling.resolve_scope_root", return_value=_FAKE_ROOT):
            with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", side_effect=_make_grep_mock_3431()):
                assert rule.check(body, []) == []


# ---------------------------------------------------------------------------
# AC-5: root_unavailable when clone is absent
# ---------------------------------------------------------------------------

