"""Tests for issuesmith.gates public API and Verdict unification (#3506)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import issuesmith.gates as gates_mod
from issuesmith.gates import Verdict, check_deps, check_m2, check_pr_scope, check_scope

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


# ---------------------------------------------------------------------------
# check_scope
# ---------------------------------------------------------------------------


def test_check_scope_returns_verdict_passed_when_within_limits(tmp_path: Path) -> None:
    from issuesmith.config import ScopeGateConfig
    from issuesmith.steps.scope_gate import ScopeMeasure, ScopeVerdict

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
    from issuesmith.steps.scope_gate import ScopeMeasure, ScopeVerdict

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
    from ghdag.workflow.gates import Violation

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


# ---------------------------------------------------------------------------
# check_m2
# ---------------------------------------------------------------------------


def test_check_m2_returns_verdict_passed_on_proceed() -> None:
    gate_result = {"action": "proceed", "unchecked_count": 0, "has_section": True,
                   "has_migration_label": False, "contract_failures": []}
    with patch("issuesmith.gates.m2.check_gate", return_value=gate_result):
        result = check_m2("## Acceptance Criteria\n- [x] done\n", [])
    assert isinstance(result, Verdict)
    assert result.passed is True


def test_check_m2_returns_verdict_failed_on_retry_with_unchecked() -> None:
    gate_result = {"action": "retry", "unchecked_count": 2, "has_section": True,
                   "has_migration_label": False, "contract_failures": []}
    with patch("issuesmith.gates.m2.check_gate", return_value=gate_result):
        result = check_m2("## Acceptance Criteria\n- [ ] todo\n", [])
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert any("2" in r for r in result.reasons)


def test_check_m2_returns_verdict_failed_on_retry_with_contract_failures() -> None:
    gate_result = {"action": "retry", "unchecked_count": 0, "has_section": True,
                   "has_migration_label": False, "contract_failures": ["path missing: src/x.py"]}
    with patch("issuesmith.gates.m2.check_gate", return_value=gate_result):
        result = check_m2("body text", [])
    assert isinstance(result, Verdict)
    assert result.passed is False
    assert "path missing: src/x.py" in result.reasons


def test_check_m2_returns_verdict_failed_on_migrate() -> None:
    gate_result = {"action": "migrate", "unchecked_count": 1, "has_section": True,
                   "has_migration_label": True, "contract_failures": []}
    with patch("issuesmith.gates.m2.check_gate", return_value=gate_result):
        result = check_m2("body text", ["scope:migration"])
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
    from issuesmith.steps.scope_gate import ScopeMeasure, ScopeVerdict

    cfg = ScopeGateConfig(enabled=True, max_files=100, max_lines=5000, hard_max_files=200)
    measure = ScopeMeasure(files=1, lines=10, by_dir={}, skipped_binary=0, skipped_jsonl=0)
    scope_verdict_raw = ScopeVerdict(exceeded=False, reason="", measure=measure)
    gate_result = {"action": "proceed", "unchecked_count": 0, "has_section": True,
                   "has_migration_label": False, "contract_failures": []}

    with (
        patch("issuesmith.gates.scope.measure_scope", return_value=measure),
        patch("issuesmith.gates.scope.evaluate", return_value=scope_verdict_raw),
        patch("issuesmith.gates.pr_scope.check_pr_diff_scope", return_value=[]),
        patch("issuesmith.gates.m2.check_gate", return_value=gate_result),
    ):
        scope_v = check_scope(Path("."), ["src/**"], cfg)
        pr_v = check_pr_scope([], [])
        m2_v = check_m2("body text", [])
        dep_v = check_deps([])

    for v in (scope_v, pr_v, m2_v, dep_v):
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
