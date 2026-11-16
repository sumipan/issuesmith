"""_commit_if_needed with changes already staged in the index (#5207)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.ops.publish import (
    _commit_if_needed,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _name_status(repo: Path) -> list[str]:
    out = _git(repo, "show", "--name-status", "--format=", "HEAD")
    return [line for line in out.splitlines() if line.strip()]


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "commit.gpgsign", "false")
    for name in ("a.py", "b.py", "other.py"):
        (repo / name).write_text(f"# {name}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_unstaged_deletion_is_committed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "a.py").unlink()

    _commit_if_needed(repo, 1, ["a.py"])

    assert "D\ta.py" in _name_status(repo)


def test_staged_rename_is_committed(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "mv", "a.py", "c.py")

    _commit_if_needed(repo, 1, ["a.py", "c.py"])

    files = _git(repo, "ls-tree", "--name-only", "HEAD").splitlines()
    assert "c.py" in files
    assert "a.py" not in files


def test_no_candidates_no_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    before = _head(repo)
    (repo / "other.py").write_text("# changed\n")

    _commit_if_needed(repo, 1, ["b.py"])

    assert _head(repo) == before


