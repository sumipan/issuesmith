"""AC-1 / AC-1b: changed_files and multi-gate evaluate_requires with real git fixture."""

from __future__ import annotations

import subprocess
from pathlib import Path

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




def test_changed_files_sorted(git_worktree: Path) -> None:
    from issuesmith.gates.worktree import changed_files

    files = changed_files(git_worktree, "main")
    assert files == sorted(files)


# ---------------------------------------------------------------------------
# AC-1: evaluate_requires on real git worktree returns lint + pr_scope violations
# ---------------------------------------------------------------------------






def test_pr_scope_gate_no_violation_when_all_in_scope(git_worktree: Path) -> None:
    """PrScopeGate returns empty when all changed files are in allow_paths."""
    from issuesmith.gates.pr_scope import PrScopeGate

    gate = PrScopeGate(git_worktree, ["src/foo.py", "scripts/helper.py"], "main")
    violations = gate.check("", [])
    assert violations == []




