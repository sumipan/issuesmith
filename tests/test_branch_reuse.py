"""tests/test_branch_reuse.py — unit tests for branch_reuse.py"""
from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith import branch_reuse


def _git_init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (path / "README").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _commit(repo: Path, message: str, filename: str = "f.txt") -> str:
    f = repo / filename
    f.write_text(message + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(f)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    return sha


def _create_branch(repo: Path, branch: str, start: str = "main") -> None:
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", branch, start],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )


def _checkout(repo: Path, branch: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "checkout", branch],
        check=True,
        capture_output=True,
    )


# --- record_base ---


def test_record_base_silences_failure(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no-git"
    branch_reuse.record_base(nonexistent, "feat/issue-7-aaaa1111", "main")


# --- find_reusable_branch: AC-1 ---


# --- find_reusable_branch: AC-2 (base changed) ---


def test_find_reusable_branch_wrong_base_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-aaaa1111"],
        check=True,
        capture_output=True,
    )
    _commit(repo, "feat work")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "develop")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


# --- find_reusable_branch: AC-2b (edge cases) ---


def test_find_reusable_branch_no_commits_ahead_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-aaaa1111", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


def test_find_reusable_branch_nonexistent_repo_returns_none(tmp_path: Path) -> None:
    result = branch_reuse.find_reusable_branch(tmp_path / "no-such-dir", 7, "main")
    assert result is None


def test_find_reusable_branch_not_a_git_repo_returns_none(tmp_path: Path) -> None:
    notgit = tmp_path / "notgit"
    notgit.mkdir()
    result = branch_reuse.find_reusable_branch(notgit, 7, "main")
    assert result is None


def test_find_reusable_branch_no_base_ref_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    result = branch_reuse.find_reusable_branch(repo, 7, "nonexistent-base")
    assert result is None


# --- find_reusable_branch: AC-2c (no false positives) ---


# --- previous_commits: AC-3 ---


def test_previous_commits_no_base_ref_returns_empty(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    result = branch_reuse.previous_commits(repo, "feat/issue-7-aaaa1111", "nonexistent")
    assert result == []


def test_previous_commits_git_failure_returns_empty(tmp_path: Path) -> None:
    result = branch_reuse.previous_commits(tmp_path / "no-git", "branch", "main")
    assert result == []


# --- is_base_recorded ---


def test_is_base_recorded_true_when_set(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    _create_branch(repo, "feat/issue-7-aaaa1111")
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")
    assert branch_reuse.is_base_recorded(repo, "feat/issue-7-aaaa1111") is True


def test_is_base_recorded_false_when_not_set(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    _create_branch(repo, "feat/issue-7-aaaa1111")
    assert branch_reuse.is_base_recorded(repo, "feat/issue-7-aaaa1111") is False


def test_is_base_recorded_false_nonexistent_repo(tmp_path: Path) -> None:
    assert branch_reuse.is_base_recorded(tmp_path / "no-git", "feat/issue-7-aaaa1111") is False


# --- AC-2b: unrecorded branch edge cases ---


# --- AC-2c: unrecorded (newer) wins over recorded (older) ---


