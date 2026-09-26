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
# m1.version_behind_base: VersionBehindBaseGate (nexus #3936)
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _write_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8"
    )


def _version_behind_repo(
    tmp_path: Path, *, start: str, branch: str | None, base: str | None
) -> Path:
    """origin (bare) + a clone on ``feat`` whose pyproject diverged from ``main``.

    ``branch`` / ``base`` are the versions committed on ``feat`` / ``origin/main``
    after the fork (None = the line is left at ``start``).
    """
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", str(origin), str(seed))
    for repo in (seed,):
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
    _write_version(seed, start)
    (seed / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "init")
    _git(seed, "push", "origin", "HEAD:main")

    wt = tmp_path / "wt"
    _git(tmp_path, "clone", str(origin), str(wt))
    _git(wt, "config", "user.email", "t@t.com")
    _git(wt, "config", "user.name", "T")
    _git(wt, "checkout", "-b", "feat")
    (wt / "notes.txt").write_text("feature\n", encoding="utf-8")
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", "docs: add notes")
    if branch is not None:
        _write_version(wt, branch)
        _git(wt, "commit", "-am", f"chore: bump version to {branch} (Z: Z)")
    _git(wt, "push", "-u", "origin", "feat")

    if base is not None:
        _write_version(seed, base)
        _git(seed, "commit", "-am", f"chore: bump version to {base} (Z: Z)")
        _git(seed, "push", "origin", "HEAD:main")
    return wt


def _in_process_bump(worktree: Path, base_branch: str):
    """Stand-in for publish._run_version_bump without the nexus scripts/ shim."""
    import contextlib
    import io
    import subprocess

    from issuesmith.ops.version_bump import run_bump

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = run_bump(worktree, base_branch)
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out.getvalue(), stderr="")


def _pyproject_version_at(repo: Path, ref: str) -> str:
    import re

    text = _git(repo, "show", f"{ref}:pyproject.toml")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m
    return m.group(1)


def test_version_behind_base_passes_without_pyproject(tmp_path: Path) -> None:
    """AC-3: a target without pyproject.toml (nexus) never violates."""
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = tmp_path / "wt"
    wt.mkdir()
    _git(tmp_path, "init", "-b", "main", str(wt))
    assert VersionBehindBaseGate(wt, "main").check("", []) == []


def test_version_behind_base_equal_versions_violates(tmp_path: Path) -> None:
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = _version_behind_repo(tmp_path, start="0.80.0", branch="0.81.0", base="0.81.0")
    violations = VersionBehindBaseGate(wt, "main").check("", [])
    assert [v.rule_id for v in violations] == ["m1.version_behind_base"]
    assert violations[0].auto_fixable is True
    assert "0.81.0" in violations[0].message


def test_version_behind_base_branch_ahead_passes(tmp_path: Path) -> None:
    """AC-2: branch version > base version → no violation, fix() adds no commit."""
    from issuesmith.gates.base import ContractInput
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = _version_behind_repo(tmp_path, start="0.80.0", branch="0.81.0", base=None)
    gate = VersionBehindBaseGate(wt, "main")
    assert gate.check("", []) == []
    head = _git(wt, "rev-parse", "HEAD")
    with patch("issuesmith.gates.m1.run_version_bump", side_effect=_in_process_bump) as bump:
        gate.fix(ContractInput(body=""))
    bump.assert_not_called()
    assert _git(wt, "rev-parse", "HEAD") == head


def test_version_behind_base_fix_equal_bumps_and_pushes(tmp_path: Path) -> None:
    """AC-1 / AC-6: 0.81.0 == 0.81.0 → 0.81.1 pushed; check() is clean afterwards."""
    from issuesmith.gates.base import ContractInput
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = _version_behind_repo(tmp_path, start="0.80.0", branch="0.81.0", base="0.81.0")
    gate = VersionBehindBaseGate(wt, "main")
    with patch("issuesmith.gates.m1.run_version_bump", side_effect=_in_process_bump):
        gate.fix(ContractInput(body=""))
    assert _pyproject_version_at(wt, "HEAD") == "0.81.1"
    _git(wt, "fetch", "origin")
    assert _pyproject_version_at(wt, "origin/feat") == "0.81.1"
    assert _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD") == ""
    assert gate.check("", []) == []


def test_version_behind_base_fix_branch_older_bumps_past_base(tmp_path: Path) -> None:
    """AC-5: branch 0.80.0 < base 0.81.0 → 0.81.1 (not 0.80.1)."""
    from issuesmith.gates.base import ContractInput
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = _version_behind_repo(tmp_path, start="0.80.0", branch=None, base="0.81.0")
    gate = VersionBehindBaseGate(wt, "main")
    assert [v.rule_id for v in gate.check("", [])] == ["m1.version_behind_base"]
    with patch("issuesmith.gates.m1.run_version_bump", side_effect=_in_process_bump):
        gate.fix(ContractInput(body=""))
    assert _pyproject_version_at(wt, "HEAD") == "0.81.1"
    assert gate.check("", []) == []


def test_version_behind_base_fix_is_idempotent(tmp_path: Path) -> None:
    """AC-6: a second fix() after a successful one adds no commit."""
    from issuesmith.gates.base import ContractInput
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = _version_behind_repo(tmp_path, start="0.80.0", branch="0.81.0", base="0.81.0")
    gate = VersionBehindBaseGate(wt, "main")
    with patch("issuesmith.gates.m1.run_version_bump", side_effect=_in_process_bump):
        gate.fix(ContractInput(body=""))
        head = _git(wt, "rev-parse", "HEAD")
        gate.fix(ContractInput(body=""))
    assert _git(wt, "rev-parse", "HEAD") == head
    assert _pyproject_version_at(wt, "HEAD") == "0.81.1"


def test_version_behind_base_fix_failure_rolls_back(tmp_path: Path) -> None:
    """A failed bump leaves HEAD (and origin/feat) where they were and raises."""
    import subprocess

    from issuesmith.gates.base import ContractInput
    from issuesmith.gates.m1 import VersionBehindBaseGate

    wt = _version_behind_repo(tmp_path, start="0.80.0", branch="0.81.0", base="0.81.0")
    head = _git(wt, "rev-parse", "HEAD")
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("issuesmith.gates.m1.run_version_bump", return_value=failed):
        with pytest.raises(RuntimeError, match="boom"):
            VersionBehindBaseGate(wt, "main").fix(ContractInput(body=""))
    assert _git(wt, "rev-parse", "HEAD") == head
    assert _git(wt, "rev-parse", "origin/feat") == head


def test_publish_exports_bump_helpers() -> None:
    from issuesmith.ops import publish

    assert "_run_version_bump" in publish.__all__
    assert "_bump_commits_on_top" in publish.__all__
    assert publish.run_version_bump is publish._run_version_bump
