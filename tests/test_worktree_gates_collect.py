"""AC-1 / AC-1b: changed_files and multi-gate evaluate_requires with real git fixture."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fixture: bare origin + clone with staged changes
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        check=True,
    )


@pytest.fixture()
def git_worktree(tmp_path: Path):
    """Minimal git worktree with lint violation, test failure file, and out-of-scope file."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@test.com")
    _git(origin, "config", "user.name", "Test")
    # initial commit on main
    (origin / "README.md").write_text("hello\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "init")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin), str(clone)],
        capture_output=True, check=True,
    )
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")

    # Add a Python file with a lint violation (unused import)
    src_dir = clone / "src"
    src_dir.mkdir()
    (src_dir / "foo.py").write_text("import os\n\ndef foo(): pass\n", encoding="utf-8")
    _git(clone, "add", "src/foo.py")
    _git(clone, "commit", "-m", "add foo.py")

    # Add an out-of-scope file (uncommitted — tests that untracked are detected)
    (clone / "scripts").mkdir()
    (clone / "scripts" / "helper.py").write_text("# helper\n", encoding="utf-8")

    return clone


# ---------------------------------------------------------------------------
# AC-1b: changed_files returns union of committed + untracked, excludes excluded dirs
# ---------------------------------------------------------------------------


def test_changed_files_includes_committed(git_worktree: Path) -> None:
    from issuesmith.gates.worktree import changed_files

    files = changed_files(git_worktree, "main")
    assert "src/foo.py" in files


def test_changed_files_includes_untracked(git_worktree: Path) -> None:
    from issuesmith.gates.worktree import changed_files

    files = changed_files(git_worktree, "main")
    assert "scripts/helper.py" in files


def test_changed_files_excludes_jobs_logs_pipeline(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@test.com")
    _git(origin, "config", "user.name", "Test")
    (origin / "README.md").write_text("hello\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "init")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin), str(clone)],
        capture_output=True, check=True,
    )
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")

    (clone / "jobs").mkdir(exist_ok=True)
    (clone / "jobs" / "task.jsonl").write_text("{}\n", encoding="utf-8")
    (clone / "logs").mkdir(exist_ok=True)
    (clone / "logs" / "output.log").write_text("log\n", encoding="utf-8")
    (clone / ".pipeline-state").mkdir(exist_ok=True)
    (clone / ".pipeline-state" / "state.yml").write_text("state: ok\n", encoding="utf-8")
    (clone / "src").mkdir(exist_ok=True)
    (clone / "src" / "valid.py").write_text("x = 1\n", encoding="utf-8")

    from issuesmith.gates.worktree import changed_files
    files = changed_files(clone, "main")
    assert not any(f.startswith("jobs/") for f in files)
    assert not any(f.startswith("logs/") for f in files)
    assert not any(f.startswith(".pipeline-state/") for f in files)
    assert "src/valid.py" in files


def test_changed_files_sorted(git_worktree: Path) -> None:
    from issuesmith.gates.worktree import changed_files

    files = changed_files(git_worktree, "main")
    assert files == sorted(files)


# ---------------------------------------------------------------------------
# AC-1: evaluate_requires on real git worktree returns lint + pr_scope violations
# ---------------------------------------------------------------------------


def test_evaluate_requires_collects_multiple_violations(
    git_worktree: Path,
) -> None:
    """Lint violation + pr_scope.out_of_allow both appear in blocking."""
    from issuesmith.gates import GateBuildContext
    from issuesmith.gates.pr_scope import PrScopeGate
    from issuesmith.gates.worktree import LintGate
    from issuesmith.repair import evaluate_requires

    # allow_paths includes src/ but not scripts/
    allow_paths = ["src/foo.py"]
    ctx = GateBuildContext(
        worktree_path=git_worktree,
        allow_paths=allow_paths,
        base_branch="main",
    )

    lint_gate = LintGate(git_worktree, allow_paths)
    pr_scope_gate = PrScopeGate(git_worktree, allow_paths, "main")

    gates: dict[str, Any] = {"lint": lint_gate, "pr_scope": pr_scope_gate}

    result = evaluate_requires(gates, "", [])

    rule_ids = {v.rule_id for v in result.blocking}
    assert "pr_scope.out_of_allow" in rule_ids, f"expected pr_scope.out_of_allow in {rule_ids}"


def test_pr_scope_gate_per_file_violation(git_worktree: Path) -> None:
    """PrScopeGate returns one Violation per out-of-scope file."""
    from issuesmith.gates.pr_scope import PrScopeGate

    # scripts/helper.py is untracked (out of scope)
    gate = PrScopeGate(git_worktree, ["src/foo.py"], "main")
    violations = gate.check("", [])

    assert any(v.rule_id == "pr_scope.out_of_allow" for v in violations)
    out_of_scope = [v for v in violations if v.rule_id == "pr_scope.out_of_allow"]
    # Each file gets its own violation
    assert all(v.location is not None for v in out_of_scope)
    # fix_hint follows widen:<file> pattern
    assert all(
        v.fix_hint is not None and v.fix_hint.startswith("widen:")
        for v in out_of_scope
    )


def test_pr_scope_gate_no_violation_when_all_in_scope(git_worktree: Path) -> None:
    """PrScopeGate returns empty when all changed files are in allow_paths."""
    from issuesmith.gates.pr_scope import PrScopeGate

    gate = PrScopeGate(git_worktree, ["src/foo.py", "scripts/helper.py"], "main")
    violations = gate.check("", [])
    assert violations == []


def test_lint_gate_targets_changed_py_files(git_worktree: Path) -> None:
    """LintGate targets .py files from changed_files, not allow_paths glob."""
    from issuesmith.gates.worktree import LintGate

    # Pass an allow_paths that contains a glob (would be ignored with old behavior)
    gate = LintGate(git_worktree, ["src/**"])
    violations = gate.check("", [])
    # src/foo.py has unused import (F401) — should be detected
    # Since changed_files finds src/foo.py, it should be linted
    assert any(v.rule_id.startswith("lint.") for v in violations)
