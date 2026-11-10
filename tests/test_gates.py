"""Tests for issuesmith.gates public API and Verdict unification (#3506)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import issuesmith.gates as gates_mod
from issuesmith.gates import Verdict, check_ac_complete, check_deps, check_pr_scope, check_scope
from tests.conventions._util import REPO_ROOT

# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------


def test_verdict_passed_has_empty_reasons() -> None:
    v = Verdict(passed=True)
    assert v.passed is True
    assert v.reasons == []


def test_verdict_failed_with_reasons() -> None:
    v = Verdict(passed=False, reasons=["too many files"])
    assert v.passed is False
    assert v.reasons == ["too many files"]


def test_verdict_is_frozen() -> None:
    v = Verdict(passed=True)
    with pytest.raises((AttributeError, TypeError)):
        v.passed = False  # type: ignore[misc]


def test_verdict_equality() -> None:
    assert Verdict(passed=True) == Verdict(passed=True, reasons=[])
    assert Verdict(passed=False, reasons=["x"]) != Verdict(passed=False, reasons=["y"])


# ---------------------------------------------------------------------------
# gates.__init__ exports
# ---------------------------------------------------------------------------


def test_all_gates_are_importable() -> None:
    for name in gates_mod.__all__:
        assert hasattr(gates_mod, name), f"missing: {name}"


def test_pr_scope_importable_first() -> None:
    """Importing pr_scope before issuesmith.gates must not hit a circular import (#6349)."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-c", "import issuesmith.pr_scope"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]


# ---------------------------------------------------------------------------
# check_scope
# ---------------------------------------------------------------------------


def test_check_scope_returns_verdict_passed_when_within_limits(tmp_path: Path) -> None:
    from issuesmith.config import ScopeGateConfig
    from issuesmith.scope_gate import ScopeMeasure, ScopeVerdict

    cfg = ScopeGateConfig(enabled=True, max_files=100, max_lines=5000, hard_max_files=200)
    measure = ScopeMeasure(files=2, lines=50, by_dir={}, skipped_binary=0, skipped_jsonl=0)
    verdict_raw = ScopeVerdict(exceeded=False, reason="", measure=measure)

    with (
        patch("issuesmith.gates.scope.measure_scope", return_value=measure),
        patch("issuesmith.gates.scope.evaluate", return_value=verdict_raw),
    ):
        result = check_scope(tmp_path, ["src/**"], cfg)
    assert isinstance(result, Verdict)
    assert result.passed is True


def test_check_scope_returns_verdict_failed_when_exceeded(tmp_path: Path) -> None:
    from issuesmith.config import ScopeGateConfig
    from issuesmith.scope_gate import ScopeMeasure, ScopeVerdict

    cfg = ScopeGateConfig(enabled=True, max_files=5, max_lines=100, hard_max_files=10)
    measure = ScopeMeasure(files=20, lines=500, by_dir={}, skipped_binary=0, skipped_jsonl=0)
    verdict_raw = ScopeVerdict(exceeded=True, reason="files: 20 > 5", measure=measure)

    with (
        patch("issuesmith.gates.scope.measure_scope", return_value=measure),
        patch("issuesmith.gates.scope.evaluate", return_value=verdict_raw),
    ):
        result = check_scope(tmp_path, ["src/**"], cfg)
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert "files: 20 > 5" in result.reasons


# ---------------------------------------------------------------------------
# check_pr_scope
# ---------------------------------------------------------------------------


def test_check_pr_scope_returns_verdict_passed_when_in_scope() -> None:
    with patch("issuesmith.gates.pr_scope.check_pr_diff_scope", return_value=[]):
        result = check_pr_scope(["src/foo.py"], ["src/**"])
    assert isinstance(result, Verdict)
    assert result.passed is True
    assert result.reasons == []


def test_check_pr_scope_returns_verdict_failed_when_out_of_scope() -> None:
    from issuesmith.gate_registry import Violation

    violation = Violation(
        rule_id="pr_diff_scope.out_of_scope",
        severity="fail",
        message="out of scope: docs/README.md (not in allow_paths)",
        location="docs/README.md",
        auto_fixable=False,
        fix_hint=None,
    )
    with patch("issuesmith.gates.pr_scope.check_pr_diff_scope", return_value=[violation]):
        result = check_pr_scope(["docs/README.md"], ["src/**"])
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert any("out of scope" in r for r in result.reasons)


def test_check_pr_scope_dot_slash_allow_path_passes() -> None:
    """#4813: ``./`` prefixed allow_paths match diff filenames without the prefix."""
    result = check_pr_scope(["README.md"], ["./README.md"], [])
    assert result.passed is True


def test_pr_scope_gate_allows_dot_slash_allow_path(tmp_path: Path) -> None:
    """#4813: PrScopeGate treats ``./README.md`` as allowing ``README.md``."""
    from issuesmith.gates.pr_scope import PrScopeGate

    gate = PrScopeGate(tmp_path, ["./README.md"], "main")
    with patch("issuesmith.gates.worktree.changed_files", return_value=["README.md"]):
        assert gate.check("", []) == []


def test_pr_scope_gate_still_rejects_other_file_with_dot_slash_allow(tmp_path: Path) -> None:
    from issuesmith.gates.pr_scope import PrScopeGate

    gate = PrScopeGate(tmp_path, ["./README.md"], "main")
    with patch("issuesmith.gates.worktree.changed_files", return_value=["src/a.py"]):
        violations = gate.check("", [])
    assert [v.location for v in violations] == ["src/a.py"]
    assert violations[0].rule_id == "pr_scope.out_of_allow"


# ---------------------------------------------------------------------------
# check_ac_complete
# ---------------------------------------------------------------------------


def test_check_ac_complete_returns_verdict_passed_on_proceed() -> None:
    gate_result = {"action": "proceed", "unchecked_count": 0, "has_section": True,
                   "has_migration_label": False, "contract_failures": []}
    with patch("issuesmith.gates.ac_complete.check_gate", return_value=gate_result):
        result = check_ac_complete("## Acceptance Criteria\n- [x] done\n", [])
    assert isinstance(result, Verdict)
    assert result.passed is True


def test_check_ac_complete_returns_verdict_failed_on_retry_with_unchecked() -> None:
    gate_result = {"action": "retry", "unchecked_count": 2, "has_section": True,
                   "has_migration_label": False, "contract_failures": []}
    with patch("issuesmith.gates.ac_complete.check_gate", return_value=gate_result):
        result = check_ac_complete("## Acceptance Criteria\n- [ ] todo\n", [])
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert any("2" in r for r in result.reasons)


def test_check_ac_complete_returns_verdict_failed_on_retry_with_contract_failures() -> None:
    gate_result = {"action": "retry", "unchecked_count": 0, "has_section": True,
                   "has_migration_label": False, "contract_failures": ["path missing: src/x.py"]}
    with patch("issuesmith.gates.ac_complete.check_gate", return_value=gate_result):
        result = check_ac_complete("body text", [])
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert "path missing: src/x.py" in result.reasons


def test_check_ac_complete_returns_verdict_failed_on_migrate() -> None:
    gate_result = {"action": "migrate", "unchecked_count": 1, "has_section": True,
                   "has_migration_label": True, "contract_failures": []}
    with patch("issuesmith.gates.ac_complete.check_gate", return_value=gate_result):
        result = check_ac_complete("body text", ["scope:migration"])
    assert isinstance(result, Verdict)
    assert result.passed is False


# ---------------------------------------------------------------------------
# check_deps
# ---------------------------------------------------------------------------


def test_check_deps_returns_verdict_passed_when_no_deps() -> None:
    result = check_deps([])
    assert isinstance(result, Verdict)
    assert result.passed is True
    assert result.reasons == []


def test_check_deps_returns_verdict_passed_when_all_satisfied() -> None:
    from issuesmith.dep_extractor import DepCheckResult

    dep_result = DepCheckResult(
        decision="PASS",
        deps_found=[100],
        blocking_deps=[],
        dep_statuses=[],
    )
    with patch("issuesmith.gates.dep.check_dependencies", return_value=dep_result):
        result = check_deps([100])
    assert isinstance(result, Verdict)
    assert result.passed is True


def test_check_deps_returns_verdict_failed_when_blocked() -> None:
    from issuesmith.dep_extractor import DepCheckResult, DepStatus

    blocker = DepStatus(issue=200, state="OPEN", has_merge_done=False,
                        rescue_pr=None, title="Dep", is_exempt=False)
    dep_result = DepCheckResult(
        decision="BLOCK",
        deps_found=[200],
        blocking_deps=[blocker],
        dep_statuses=[blocker],
    )
    with patch("issuesmith.gates.dep.check_dependencies", return_value=dep_result):
        result = check_deps([200])
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert any("#200" in r for r in result.reasons)


# ---------------------------------------------------------------------------
# All gate functions return Verdict instances
# ---------------------------------------------------------------------------


def test_all_gate_functions_return_verdict_type() -> None:
    """Smoke test: each gate returns a Verdict, not a legacy type."""
    from issuesmith.config import ScopeGateConfig
    from issuesmith.scope_gate import ScopeMeasure, ScopeVerdict

    cfg = ScopeGateConfig(enabled=True, max_files=100, max_lines=5000, hard_max_files=200)
    measure = ScopeMeasure(files=1, lines=10, by_dir={}, skipped_binary=0, skipped_jsonl=0)
    scope_verdict_raw = ScopeVerdict(exceeded=False, reason="", measure=measure)
    gate_result = {"action": "proceed", "unchecked_count": 0, "has_section": True,
                   "has_migration_label": False, "contract_failures": []}

    with (
        patch("issuesmith.gates.scope.measure_scope", return_value=measure),
        patch("issuesmith.gates.scope.evaluate", return_value=scope_verdict_raw),
        patch("issuesmith.gates.pr_scope.check_pr_diff_scope", return_value=[]),
        patch("issuesmith.gates.ac_complete.check_gate", return_value=gate_result),
    ):
        scope_v = check_scope(Path("."), ["src/**"], cfg)
        pr_v = check_pr_scope([], [])
        ac_v = check_ac_complete("body text", [])
        dep_v = check_deps([])

    for v in (scope_v, pr_v, ac_v, dep_v):
        assert isinstance(v, Verdict), f"expected Verdict, got {type(v)}"


# ---------------------------------------------------------------------------
# AC-2b: GATE_REGISTRY.build() returns an object with check(body, labels) (#3671)
# ---------------------------------------------------------------------------


def test_gate_registry_all_entries_build_successfully(tmp_path: Path) -> None:
    """Every GATE_REGISTRY entry must produce a gate with check() when given a valid context."""
    import subprocess

    from issuesmith.gates import GATE_REGISTRY, GateBuildContext

    # Create a minimal git repo as worktree_path for worktree gates.
    git_root = tmp_path / "repo"
    git_root.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(git_root)], capture_output=True, check=False)
    subprocess.run(["git", "-C", str(git_root), "config", "user.email", "t@t.com"],
                   capture_output=True, check=False)
    subprocess.run(["git", "-C", str(git_root), "config", "user.name", "T"],
                   capture_output=True, check=False)
    subprocess.run(["git", "-C", str(git_root), "commit", "--allow-empty", "-m", "init"],
                   capture_output=True, check=False)

    ctx_worktree = GateBuildContext(
        worktree_path=git_root,
        allow_paths=["src/**"],
        base_branch="main",
    )
    ctx_issue = GateBuildContext(
        worktree_path=None,
        allow_paths=[],
        base_branch="main",
    )

    for gate_id, entry in GATE_REGISTRY.items():
        ctx = ctx_worktree if entry.input_kind == "worktree" else ctx_issue
        gate = entry.build(ctx)
        assert hasattr(gate, "check"), f"gate {gate_id!r} missing check()"
        result = gate.check("body text", [])
        assert isinstance(result, list), f"gate {gate_id!r} check() must return list"


def test_worktree_gate_build_fails_without_worktree_path() -> None:
    """Worktree gates raise GateBuildError when worktree_path is None."""
    from issuesmith.gates import GATE_REGISTRY, GateBuildContext, GateBuildError

    ctx_no_wt = GateBuildContext(worktree_path=None, allow_paths=[], base_branch="main")
    for gate_id, entry in GATE_REGISTRY.items():
        if entry.input_kind == "worktree":
            with pytest.raises(GateBuildError):
                entry.build(ctx_no_wt)


# ---------------------------------------------------------------------------
# AC-2d: DepsGate checks merge state via issue body extraction (#3671)
# ---------------------------------------------------------------------------


def test_deps_gate_no_deps_returns_empty() -> None:
    from issuesmith.gates.dep import DepsGate

    gate = DepsGate()
    body = "## Background\n\nNo dependencies here."
    result = gate.check(body, [])
    assert result == []


def test_deps_gate_self_reference_only_returns_empty() -> None:
    from issuesmith.gates.dep import DepsGate

    body = "## Dependencies\n\nnotes for #5061 in #5061\n"
    assert DepsGate(issue_number=5061).check(body, []) == []


def test_deps_gate_self_reference_without_issue_number_still_fails() -> None:
    from issuesmith.gates.dep import DepsGate

    body = "## Dependencies\n\nnotes for #5061 in #5061\n"
    result = DepsGate().check(body, [])
    assert len(result) == 1
    assert result[0].rule_id == "deps.unparsed_dependency_section"


def test_deps_format_registry_build_uses_issue_number() -> None:
    from issuesmith.gates import GATE_REGISTRY, GateBuildContext

    entry = GATE_REGISTRY["deps_format"]
    assert entry.input_kind == "issue"
    gate = entry.build(
        GateBuildContext(
            worktree_path=None,
            allow_paths=[],
            base_branch="main",
            variables={"issue_number": "5061"},
        )
    )
    body = "## Dependencies\n\nonly #5061 in prose\n"
    assert gate.check(body, []) == []


def test_deps_gate_prose_only_section_returns_unparsed_violation() -> None:
    from unittest.mock import patch

    from issuesmith.gates.dep import DepsGate

    gate = DepsGate()
    body = "## Dependencies\n\nneeds #99 first\n"

    with patch("issuesmith.gates.dep.check_deps") as mock_check:
        result = gate.check(body, [])

    mock_check.assert_not_called()
    assert len(result) == 1
    assert result[0].rule_id == "deps.unparsed_dependency_section"
    assert result[0].severity == "fail"
    assert "#99" in result[0].message


def test_check_deps_unparsed_reason_is_reported() -> None:
    from issuesmith.dep_extractor import DepCheckResult

    with patch("issuesmith.gates.dep.check_dependencies") as mock_check:
        mock_check.return_value = DepCheckResult(
            decision="BLOCK",
            deps_found=[],
            blocking_deps=[],
            dep_statuses=[],
            unparsed_refs=[99],
            reason="unparsed_dependency_section",
        )
        result = check_deps([])

    assert result.passed is False
    assert result.reasons == ["unparsed_dependency_section: #99"]


def test_deps_gate_unmerged_dep_returns_violation() -> None:
    from unittest.mock import patch

    from issuesmith.gates.dep import DepsGate

    gate = DepsGate()
    body = "## Dependencies\n\n| # | Issue |\n| --- | --- |\n| 1 | #99 |\n"

    with patch("issuesmith.gates.dep.check_deps") as mock_check:
        from issuesmith.gates import Verdict as _Verdict
        mock_check.return_value = _Verdict(passed=False, reasons=["#99 state=OPEN has_merge_done=False"])
        result = gate.check(body, [])

    assert len(result) == 1
    assert result[0].rule_id == "deps.unmerged"
    assert "#99" in result[0].message


def test_deps_gate_merged_dep_returns_empty() -> None:
    from unittest.mock import patch

    from issuesmith.gates.dep import DepsGate

    gate = DepsGate()
    body = "## Dependencies\n\n| # | Issue |\n| --- | --- |\n| 1 | #99 |\n"

    with patch("issuesmith.gates.dep.check_deps") as mock_check:
        from issuesmith.gates import Verdict as _Verdict
        mock_check.return_value = _Verdict(passed=True, reasons=[])
        result = gate.check(body, [])

    assert result == []


# ---------------------------------------------------------------------------
# git helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def test_publish_exports_bump_helpers() -> None:
    from issuesmith.ops import publish

    assert "_run_version_bump" in publish.__all__
    assert "_bump_versions_in_range" in publish.__all__
    assert publish.run_version_bump is publish._run_version_bump


def test_allow_paths_forbidden_registry_entry() -> None:
    from issuesmith.gates import GATE_REGISTRY

    entry = GATE_REGISTRY["allow_paths_forbidden"]
    assert entry.input_kind == "issue"
    assert entry.pre_llm is True
    assert entry.repairable is False


def test_non_repairable_rule_ids_and_pr_scope_repairable() -> None:
    from issuesmith.gates import GATE_REGISTRY, NON_REPAIRABLE_RULE_IDS, is_non_repairable_rule_id

    assert NON_REPAIRABLE_RULE_IDS == frozenset(
        {
            "pr_diff_scope.forbidden_path",
            "base_freshness.fetch_transient_exhausted",
        }
    )
    assert GATE_REGISTRY["pr_scope"].repairable is True
    assert is_non_repairable_rule_id("pr_diff_scope.forbidden_path") is True
    assert is_non_repairable_rule_id("review.cp2.engine_error") is True
    assert is_non_repairable_rule_id("review.cp2") is False


def test_gates_exports_only_canonical_ac_complete() -> None:
    import importlib

    import issuesmith.gates.ac_complete as gates_ac

    assert "check_ac_complete" in gates_mod.__all__
    assert gates_mod.check_ac_complete is gates_ac.check_ac_complete
    assert "check_m2" not in gates_mod.__all__
    assert not hasattr(gates_mod, "check_m2")
    assert not hasattr(gates_ac, "check_m2")
    with pytest.raises(ImportError):
        exec("from issuesmith.gates import check_m2", {})
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("issuesmith.gates.m2")


# ---------------------------------------------------------------------------
# PrScopeGate publish bump pyproject (#6177)
# ---------------------------------------------------------------------------


def _pr_scope_pyproject_repo(tmp_path: Path) -> Path:
    """Bare origin, feature branch, and origin/main ref on the worktree."""
    import subprocess

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(seed)],
        check=True,
        capture_output=True,
    )
    _git(seed, "config", "user.email", "t@t.com")
    _git(seed, "config", "user.name", "T")
    (seed / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    _git(seed, "add", "pyproject.toml")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(wt)],
        check=True,
        capture_output=True,
    )
    _git(wt, "config", "user.email", "t@t.com")
    _git(wt, "config", "user.name", "T")
    _git(wt, "checkout", "-q", "-b", "feat")
    _git(wt, "fetch", "-q", "origin", "main:refs/remotes/origin/main")
    return wt


def test_pr_scope_gate_allows_publish_bump_only_pyproject(tmp_path: Path) -> None:
    """#6177: publish bump-only pyproject diff is in scope without allow_paths entry."""
    from issuesmith.gates.pr_scope import PrScopeGate

    wt = _pr_scope_pyproject_repo(tmp_path)
    (wt / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    _git(wt, "commit", "-q", "-am", "chore: bump version to 0.2.0 (Z: Z)")
    gate = PrScopeGate(wt, ["src/**"], "main")
    assert gate.check("", []) == []


def test_pr_scope_gate_rejects_pyproject_non_version_change_after_bump(tmp_path: Path) -> None:
    """#6177: mixed pyproject edits stay out of allow after a publish bump."""
    from issuesmith.gates.pr_scope import PrScopeGate

    wt = _pr_scope_pyproject_repo(tmp_path)
    (wt / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    _git(wt, "commit", "-q", "-am", "chore: bump version to 0.2.0 (Z: Z)")
    (wt / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.2.0"\ndependencies = ["httpx"]\n',
        encoding="utf-8",
    )
    _git(wt, "commit", "-q", "-am", "feat: add dependency")
    gate = PrScopeGate(wt, ["src/**"], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_scope.out_of_allow"
    assert violations[0].location == "pyproject.toml"


def test_pr_scope_gate_rejects_version_change_without_publish_bump(tmp_path: Path) -> None:
    """#6177: hand-edited version without a publish bump commit is out of allow."""
    from issuesmith.gates.pr_scope import PrScopeGate

    wt = _pr_scope_pyproject_repo(tmp_path)
    (wt / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    _git(wt, "commit", "-q", "-am", "fix: bump version by hand")
    gate = PrScopeGate(wt, ["src/**"], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_scope.out_of_allow"
