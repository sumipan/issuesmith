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
# allow_paths is missing steps/cp2_checkpoint.py
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
    """_git_grep side_effect: run_guarded defined in engine.py, used in cp2_checkpoint."""
    def fake_grep(root, pattern, pathspec):
        if pattern == "def run_guarded":
            return ["src/issuesmith/engine.py"]
        if pattern == "class run_guarded":
            return []
        if pattern == "run_guarded" and pathspec == "src":
            return ["src/issuesmith/engine.py", "src/issuesmith/steps/cp2_checkpoint.py"]
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
    missing_src="src/issuesmith/steps/cp2_checkpoint.py",
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


class TestAC1CallersOutsideAllowPaths:
    """AC-1: run_guarded changed; cp2_checkpoint.py missing from allow_paths."""

    def test_returns_callers_violation_with_cp2_checkpoint(self):
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_ac1(),
            ):
                violations = ScopeCouplingRules().check(_AC1_BODY, [])

        caller_v = [v for v in violations if v.rule_id == "scope_coupling.callers_outside_allow_paths"]
        assert caller_v, f"Expected callers violation, got: {violations}"
        v = caller_v[0]
        assert v.severity == "fail"
        assert "steps/cp2_checkpoint.py" in v.fix_hint

    def test_no_violation_when_missing_file_in_allow_paths(self):
        body = _AC1_BODY.replace(
            "  - src/issuesmith/engine.py\n",
            "  - src/issuesmith/engine.py\n  - src/issuesmith/steps/cp2_checkpoint.py\n",
        )
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_ac1(),
            ):
                violations = ScopeCouplingRules().check(body, [])
        caller_v = [v for v in violations if v.rule_id == "scope_coupling.callers_outside_allow_paths"]
        assert not caller_v


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

    def test_widen_succeeds_when_under_max_files(self):
        rule = ScopeCouplingRules()
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_run_guarded(),
            ):
                violations = rule.check(_AC3_BODY_SMALL, [])

        assert violations
        assert all(v.auto_fixable for v in violations)
        assert rule.autofix_new_allow_paths is not None
        assert "src/issuesmith/steps/cp2_checkpoint.py" in rule.autofix_new_allow_paths

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

    def test_widen_exactly_at_max_files_succeeds(self):
        """Merged count == max_files (80): 79 allow_paths + 1 missing = 80."""
        # engine.py in allow_paths (not missing), cp2_checkpoint.py is missing
        body_79 = (
            "```yaml\n"
            "target_repo: sumipan/issuesmith\n"
            "base_branch: main\n"
            "allow_paths:\n"
            "  - src/issuesmith/engine.py\n"
            + "".join(f"  - src/issuesmith/file{i}.py\n" for i in range(78))
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
                violations = rule.check(body_79, [])

        assert violations
        assert all(v.auto_fixable for v in violations)
        assert rule.autofix_new_allow_paths is not None
        assert len(rule.autofix_new_allow_paths) == 80


class TestAC4IgnoreShortAndConfigSymbols:
    """AC-4: short keys (<=3 chars) cause no violation (ignore_symbols removed in #3628)."""

    def test_three_char_key_generates_no_grep_call(self):
        # basename of run.py is "run" (3 chars) -> filtered
        body = (
            "```yaml\n"
            "target_repo: sumipan/issuesmith\n"
            "base_branch: main\n"
            "allow_paths:\n"
            "  - src/issuesmith/run.py\n"
            "```\n\n"
            "## Design\n\n"
            "The `run` helper is changed.\n"
        )
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                return_value=["some/other/file.py"],
            ) as mock_grep:
                violations = ScopeCouplingRules().check(body, [])

        assert violations == []
        run_calls = [c for c in mock_grep.call_args_list if c.args[1] == "run"]
        assert not run_calls, "grep should not be called for 3-char key 'run'"


class TestAC5RegistryAndGateIntegration:
    """AC-5: GATE_REGISTRY contains scope_coupling; cp1_gate aggregates violations."""

    def test_gate_registry_contains_scope_coupling(self):
        from ghdag.workflow.gates import GATE_REGISTRY

        import issuesmith.gate_rules  # noqa: F401 -- ensures registration

        assert "scope_coupling" in GATE_REGISTRY
        assert GATE_REGISTRY["scope_coupling"] is ScopeCouplingRules

    def test_cp1_gate_aggregates_coupling_violations(self):
        from issuesmith.cp1_gate import check_gate

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_ac1(),
            ):
                result = check_gate(_AC1_BODY, [])

        assert result["status"] == "FAIL"
        assert any(
            "callers_outside_allow_paths" in r or "steps/cp2_checkpoint.py" in r
            for r in result["reasons"]
        )

    def test_cp1_gate_sets_autofix_when_coupling_widens(self):
        from issuesmith.cp1_gate import check_gate

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
        assert "src/issuesmith/steps/cp2_checkpoint.py" in result["autofix_new_allow_paths"]


class TestEdgeCases:
    """Edge cases: no allow_paths, no clone, no keys, invalid body."""

    def test_no_allow_paths_returns_empty(self):
        body = (
            "```yaml\n"
            "target_repo: sumipan/issuesmith\n"
            "base_branch: main\n"
            "```\n\n"
            "## Design\n"
        )
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            violations = ScopeCouplingRules().check(body, [])
        assert violations == []

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

    def test_no_matching_files_returns_empty(self):
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                return_value=[],
            ):
                violations = ScopeCouplingRules().check(_AC1_BODY, [])
        assert violations == []

    def test_invalid_body_returns_empty(self):
        violations = ScopeCouplingRules().check("not a yaml block", [])
        assert violations == []

    def test_all_hits_already_in_allow_paths_returns_empty(self):
        body = (
            "```yaml\n"
            "target_repo: sumipan/issuesmith\n"
            "base_branch: main\n"
            "allow_paths:\n"
            "  - src/issuesmith/engine.py\n"
            "  - src/issuesmith/steps/cp2_checkpoint.py\n"
            "```\n\n"
            "## Design\n\n"
            "`run_guarded` is changed.\n"
        )
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=_FAKE_ROOT,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling._git_grep",
                side_effect=_make_grep_mock_ac1(),
            ):
                violations = ScopeCouplingRules().check(body, [])
        assert violations == []


def test_disabled_gate_returns_no_violations_and_does_not_scan(monkeypatch):
    """scope_coupling.enabled=false short-circuits before any repo scan."""
    from unittest.mock import MagicMock

    from issuesmith.gate_rules import scope_coupling as sc

    cfg = MagicMock()
    cfg.scope_coupling.enabled = False
    monkeypatch.setattr(sc, "get_config", lambda: cfg)
    grep = MagicMock(side_effect=AssertionError("must not scan when disabled"))
    monkeypatch.setattr(sc, "_git_grep", grep)
    body = (
        "```yaml\ntarget_repo: sumipan/nexus\nbase_branch: main\n"
        "allow_paths:\n  - \"src/a.py\"\n```\n\n## Design\n\nuses `some_symbol_name`\n"
    )
    rule = sc.ScopeCouplingRules()
    assert rule.check(body, []) == []
    assert rule.autofix_note is None
    grep.assert_not_called()



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

    def test_required_only_from_changed_symbols_reference_in_hint(self):
        """AC-2: only callers of public symbols defined in changed files are required; string-only
        matches (the 'dispatcher' basename) are listed for reference and never widen allow_paths."""
        rule = ScopeCouplingRules()
        body = _3431_BODY.format(scope_mode="")
        with mock.patch("issuesmith.gate_rules.scope_coupling.resolve_scope_root", return_value=_FAKE_ROOT):
            with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", side_effect=_make_grep_mock_3431()):
                violations = rule.check(body, [])
        assert len(violations) == 1
        v = violations[0]
        assert v.rule_id == "scope_coupling.callers_outside_allow_paths"
        for n in ("watch", "run", "status"):
            assert f"src/ghdag/cli/commands/{n}.py" in v.message
        assert "src/ghdag/x/mod0.py" not in v.message
        assert "reference (string match only" in v.fix_hint
        assert "src/ghdag/x/mod0.py" in v.fix_hint
        assert v.auto_fixable is True
        assert rule.autofix_new_allow_paths is not None
        assert len(rule.autofix_new_allow_paths) == 2 + 3
        assert not any("x/mod" in p for p in rule.autofix_new_allow_paths)


# ---------------------------------------------------------------------------
# AC-5: root_unavailable when clone is absent
# ---------------------------------------------------------------------------

class TestRootUnavailable:
    def test_disabled_gate_no_violation_even_without_clone(self, monkeypatch):
        """AC-6: enabled=false -> no violations even when root is None."""
        from unittest.mock import MagicMock

        from issuesmith.gate_rules import scope_coupling as sc

        cfg = MagicMock()
        cfg.scope_coupling.enabled = False
        monkeypatch.setattr(sc, "get_config", lambda: cfg)
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=None,
        ):
            violations = ScopeCouplingRules().check(_AC1_BODY, [])
        assert violations == []


# ---------------------------------------------------------------------------
# AC-1 (search_dirs) and AC-1b: real git fixture
# ---------------------------------------------------------------------------

def _setup_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with files for search_dirs tests."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    def write(rel_path: str, content: str) -> None:
        p = repo / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    write("workflows/x-step.md", "# placeholder\n")
    write("tests/test_x.py", "STEP = 'x-step'\n")
    write("workflows/y.md", "x-step is used here\n")
    write("tools/z.py", "STEP = 'x-step'\n")

    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


_SEARCH_DIRS_BODY_TEMPLATE = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - workflows/x-step.md\n"
    "```\n\n"
    "## Changed Files\n\n"
    "| Repo | File | Change |\n"
    "| sumipan/issuesmith | workflows/x-step.md | delete |\n"
)


class TestSearchDirsRealGit:
    """AC-1 and AC-1b: real git fixture with workflows/ and tools/ directories."""

    def test_custom_search_dirs_finds_workflows_and_tools(self, tmp_path, monkeypatch):
        """AC-1: search_dirs=[tests,src,workflows,tools] finds workflows/y.md and tools/z.py."""
        import yaml as _yaml

        from issuesmith.config import reset_config_cache

        repo = _setup_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {
                    "enabled": True,
                    "search_dirs": ["tests", "src", "workflows", "tools"],
                },
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_SEARCH_DIRS_BODY_TEMPLATE, [])

        caller_v = [v for v in violations if v.rule_id == "scope_coupling.callers_outside_allow_paths"]
        assert caller_v, f"Expected callers violation, got: {violations}"
        msg = caller_v[0].message
        assert "tools/z.py" in msg
        assert "workflows/y.md" in msg

    def test_default_search_dirs_misses_workflows_and_tools(self, tmp_path, monkeypatch):
        """AC-1b: default search_dirs=[tests,src] does not find workflows/y.md or tools/z.py."""
        import yaml as _yaml

        from issuesmith.config import reset_config_cache

        repo = _setup_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {"enabled": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_SEARCH_DIRS_BODY_TEMPLATE, [])

        all_messages = " ".join(
            v.message for v in violations
            if v.rule_id in (
                "scope_coupling.callers_outside_allow_paths",
                "scope_coupling.tests_outside_allow_paths",
            )
        )
        assert "workflows/y.md" not in all_messages
        assert "tools/z.py" not in all_messages

    def test_allow_paths_containing_found_files_no_violation(self, tmp_path, monkeypatch):
        """AC-1 variant: with missing files in allow_paths, no violation."""
        import yaml as _yaml

        from issuesmith.config import reset_config_cache

        repo = _setup_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {
                    "enabled": True,
                    "search_dirs": ["tests", "src", "workflows", "tools"],
                },
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        body_with_all = _SEARCH_DIRS_BODY_TEMPLATE.replace(
            "  - workflows/x-step.md\n",
            (
                "  - workflows/x-step.md\n"
                "  - tests/test_x.py\n"
                "  - workflows/y.md\n"
                "  - tools/z.py\n"
            ),
        )
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(body_with_all, [])

        missing_v = [
            v for v in violations
            if v.rule_id in (
                "scope_coupling.callers_outside_allow_paths",
                "scope_coupling.tests_outside_allow_paths",
            )
        ]
        assert missing_v == [], f"Expected no missing violations, got: {missing_v}"


# ---------------------------------------------------------------------------
# AC-4: removal names in tables and headings (real git fixture)
# ---------------------------------------------------------------------------

_REMOVAL_TABLE_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/pkg/main.py\n"
    "```\n\n"
    "## Changed Files\n\n"
    "| Repo | File | Change |\n"
    "| sumipan/issuesmith | src/pkg/main.py | update |\n\n"
    "## Removal Details\n\n"
    "The following are being removed:\n\n"
    "| Symbol | Change |\n"
    "| `FOO_BAR` | remove |\n"
    "| `foo_bar` | remove |\n"
)

_REMOVAL_HEADING_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/pkg/main.py\n"
    "```\n\n"
    "## Items to remove: old_result\n\n"
    "The `${old_result}` template variable is no longer used.\n"
)

_REMOVAL_OUTSIDE_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/pkg/main.py\n"
    "```\n\n"
    "## Normal Section\n\n"
    "The `BAZ_QUUX` identifier is mentioned here (no removal context).\n"
)


def _setup_removal_git_repo(tmp_path: Path) -> Path:
    """Create a git repo with files containing FOO_BAR, foo_bar, old_result, BAZ_QUUX."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    def write(rel_path: str, content: str) -> None:
        p = repo / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    write("src/pkg/main.py", "FOO_BAR = 1\nfoo_bar = 2\nBAZ_QUUX = 3\n")
    write("tests/test_foo.py", "from src.pkg.main import FOO_BAR\n")
    write("src/pkg/bar.py", "from src.pkg.main import foo_bar\n")
    write("src/pkg/tmpl.py", "x = old_result\n")
    write("src/pkg/baz.py", "y = BAZ_QUUX\n")

    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestRemovalNames:
    """AC-4, AC-4b, AC-4c: removal names extracted from tables and headings."""

    def test_removal_table_required_catches_callers(self, tmp_path, monkeypatch):
        """AC-4: FOO_BAR and foo_bar in removal table -> tests/test_foo.py and src/pkg/bar.py."""
        import yaml as _yaml

        repo = _setup_removal_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {"enabled": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        from issuesmith.config import reset_config_cache
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_REMOVAL_TABLE_BODY, [])

        test_v = [v for v in violations if v.rule_id == "scope_coupling.tests_outside_allow_paths"]
        caller_v = [v for v in violations if v.rule_id == "scope_coupling.callers_outside_allow_paths"]
        assert test_v, f"Expected tests violation, got: {violations}"
        assert caller_v, f"Expected callers violation, got: {violations}"
        assert "tests/test_foo.py" in test_v[0].message
        assert "src/pkg/bar.py" in caller_v[0].message

    def test_removal_heading_template_var_required(self, tmp_path, monkeypatch):
        """AC-4b: ${old_result} in removal heading -> src/pkg/tmpl.py outside allow_paths."""
        import yaml as _yaml

        repo = _setup_removal_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {"enabled": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        from issuesmith.config import reset_config_cache
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_REMOVAL_HEADING_BODY, [])

        all_msgs = " ".join(v.message for v in violations)
        assert "src/pkg/tmpl.py" in all_msgs, f"Expected tmpl.py in violations, got: {violations}"

    def test_outside_removal_context_not_required(self, tmp_path, monkeypatch):
        """AC-4c: BAZ_QUUX outside removal context is not required -> no violation."""
        import yaml as _yaml

        repo = _setup_removal_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {"enabled": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        from issuesmith.config import reset_config_cache
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_REMOVAL_OUTSIDE_BODY, [])

        all_msgs = " ".join(v.message for v in violations)
        assert "src/pkg/baz.py" not in all_msgs, (
            f"BAZ_QUUX (no removal context) should not generate violation, got: {all_msgs}"
        )


class TestRemovalNamesUnit:
    """Unit tests for _removal_names on heading text itself."""

    def test_backtick_ident_in_removal_heading_extracted(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = "## Delete `MY_CONST`\n\nNo identifier in the body.\n"
        assert "MY_CONST" in _removal_names(body)

    def test_template_var_in_removal_heading_extracted(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = "### Remove `${old_step_result}`\n\nNo longer used.\n"
        assert "old_step_result" in _removal_names(body)

    def test_backtick_ident_in_non_removal_heading_ignored(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = "## About `KEEP_CONST`\n\nplain text\n"
        assert _removal_names(body) == set()

    def test_removal_section_closes_at_same_level_heading(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = (
            "## Items to delete\n\n`GONE_NAME` goes away.\n\n"
            "## Next section\n\n`STAY_NAME` stays.\n"
        )
        assert _removal_names(body) == {"GONE_NAME"}
