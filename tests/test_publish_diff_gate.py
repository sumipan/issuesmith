"""publish_diff worktree gate — branch diff matches publish post-commit checks (#7031)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.gates import GATE_REGISTRY, GateBuildContext

_ASSERT_LINE = '    assert project["version"] == "0.26.0"'
_RULE_ID = "design_lint.test_version_exact_assert"


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_all(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """Bare origin with origin/main and a feature clone."""
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
    _write(seed, "README.md", "hello\n")
    _write(
        seed,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.26.0"\n',
    )
    _commit_all(seed, "init")
    _git(seed, "push", "-q", "origin", "HEAD:main")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        capture_output=True,
    )
    _git(clone, "config", "user.email", "t@t.com")
    _git(clone, "config", "user.name", "T")
    _git(clone, "checkout", "-q", "-b", "feat")
    _git(clone, "fetch", "-q", "origin", "main:refs/remotes/origin/main")
    return clone


def _gate(repo: Path):
    ctx = GateBuildContext(worktree_path=repo, allow_paths=[], base_branch="main")
    return GATE_REGISTRY["publish_diff"].build(ctx)


def test_check_reports_exact_version_assert_committed_on_branch(repo: Path) -> None:
    """Committed tests/test_x.py with an exact version assert is reported on check."""
    _write(
        repo,
        "tests/test_x.py",
        f"def test_x():\n{_ASSERT_LINE}\n",
    )
    _commit_all(repo, "add test with exact version assert")

    violations = _gate(repo).check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == _RULE_ID
    assert violations[0].location == "tests/test_x.py"


def test_check_reports_exact_version_assert_in_untracked_test_file(repo: Path) -> None:
    """An untracked tests/test_y.py with the assert is reported without a commit."""
    _write(
        repo,
        "tests/test_y.py",
        f"def test_y():\n{_ASSERT_LINE}\n",
    )

    violations = _gate(repo).check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == _RULE_ID
    assert violations[0].location == "tests/test_y.py"


def test_check_ignores_version_line_of_bump_commit(repo: Path) -> None:
    """A publish bump commit alone does not produce violations."""
    _write(
        repo,
        "pyproject.toml",
        '[project]\nname = "demo"\nversion = "0.26.1"\n',
    )
    _commit_all(repo, "chore: bump version to 0.26.1")

    assert _gate(repo).check("", []) == []
