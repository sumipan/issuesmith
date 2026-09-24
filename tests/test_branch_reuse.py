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


def test_record_base_writes_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    _create_branch(repo, "feat/issue-7-aaaa1111")
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")
    result = subprocess.check_output(
        ["git", "-C", str(repo), "config", "branch.feat/issue-7-aaaa1111.issuesmithbase"],
        text=True,
    ).strip()
    assert result == "main"


def test_record_base_silences_failure(tmp_path: Path) -> None:
    nonexistent = tmp_path / "no-git"
    branch_reuse.record_base(nonexistent, "feat/issue-7-aaaa1111", "main")


# --- find_reusable_branch: AC-1 ---


def test_find_reusable_branch_returns_matching(tmp_path: Path) -> None:
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
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result == "feat/issue-7-aaaa1111"


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


def test_find_reusable_branch_no_issuesmithbase_recorded(tmp_path: Path) -> None:
    """AC-2: unrecorded branch with common history, ahead of main → adopted."""
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
    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result == "feat/issue-7-aaaa1111"


def test_find_reusable_branch_merged_returns_none(tmp_path: Path) -> None:
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
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "feat/issue-7-aaaa1111"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


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


def test_find_reusable_branch_different_issue_excluded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-70-bbbb2222"],
        check=True,
        capture_output=True,
    )
    _commit(repo, "issue 70 work")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-70-bbbb2222", "main")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


def test_find_reusable_branch_diary_suffix_excluded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-aaaa1111-diary"],
        check=True,
        capture_output=True,
    )
    _commit(repo, "diary work")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111-diary", "main")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


def test_find_reusable_branch_picks_newest_of_two(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)

    env_older = {"GIT_COMMITTER_DATE": "2020-01-01T00:00:00+0000", "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+0000"}
    env_newer = {"GIT_COMMITTER_DATE": "2021-01-01T00:00:00+0000", "GIT_AUTHOR_DATE": "2021-01-01T00:00:00+0000"}
    import os
    base_env = os.environ.copy()

    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-aaaa1111"],
        check=True,
        capture_output=True,
    )
    f_old = repo / "old.txt"
    f_old.write_text("older\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(f_old)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "older work"],
        check=True,
        capture_output=True,
        env={**base_env, **env_older},
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")

    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-bbbb2222"],
        check=True,
        capture_output=True,
    )
    f_new = repo / "new.txt"
    f_new.write_text("newer\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(f_new)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "newer work"],
        check=True,
        capture_output=True,
        env={**base_env, **env_newer},
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-bbbb2222", "main")

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result == "feat/issue-7-bbbb2222"


# --- previous_commits: AC-3 ---


def test_previous_commits_returns_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-aaaa1111"],
        check=True,
        capture_output=True,
    )
    _commit(repo, "add feature X")
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )

    commits = branch_reuse.previous_commits(repo, "feat/issue-7-aaaa1111", "main")
    assert len(commits) == 1
    assert "add feature X" in commits[0]
    parts = commits[0].split(" ", 1)
    assert len(parts) == 2
    assert len(parts[0]) == 7


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


def test_find_reusable_branch_orphan_returns_none(tmp_path: Path) -> None:
    """AC-2b: orphan branch (no common history) is excluded."""
    repo = tmp_path / "repo"
    _git_init_repo(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--orphan", "feat/issue-7-orphan1"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "rm", "-rf", "."],
        capture_output=True,
        check=False,
    )
    (repo / "orphan.txt").write_text("orphan\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "orphan.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "orphan commit"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


def test_find_reusable_branch_unrecorded_merged_returns_none(tmp_path: Path) -> None:
    """AC-2b: merged unrecorded branch is excluded."""
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
    subprocess.run(
        ["git", "-C", str(repo), "merge", "--no-ff", "feat/issue-7-aaaa1111"],
        check=True,
        capture_output=True,
    )
    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


def test_find_reusable_branch_unrecorded_no_commits_ahead_returns_none(tmp_path: Path) -> None:
    """AC-2b: unrecorded branch with 0 commits ahead of main is excluded."""
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
    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result is None


# --- AC-2c: unrecorded (newer) wins over recorded (older) ---


def test_find_reusable_branch_unrecorded_newer_beats_recorded_older(tmp_path: Path) -> None:
    """AC-2c: unrecorded newer branch wins over recorded older branch."""
    import os

    repo = tmp_path / "repo"
    _git_init_repo(repo)

    env_older = {
        "GIT_COMMITTER_DATE": "2020-01-01T00:00:00+0000",
        "GIT_AUTHOR_DATE": "2020-01-01T00:00:00+0000",
    }
    env_newer = {
        "GIT_COMMITTER_DATE": "2021-01-01T00:00:00+0000",
        "GIT_AUTHOR_DATE": "2021-01-01T00:00:00+0000",
    }
    base_env = os.environ.copy()

    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-aaaa1111"],
        check=True,
        capture_output=True,
    )
    f_old = repo / "old.txt"
    f_old.write_text("older\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(f_old)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "older work"],
        check=True,
        capture_output=True,
        env={**base_env, **env_older},
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    branch_reuse.record_base(repo, "feat/issue-7-aaaa1111", "main")

    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/issue-7-bbbb2222"],
        check=True,
        capture_output=True,
    )
    f_new = repo / "new.txt"
    f_new.write_text("newer\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(f_new)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "newer work"],
        check=True,
        capture_output=True,
        env={**base_env, **env_newer},
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    # feat/issue-7-bbbb2222 intentionally has NO issuesmithbase recorded

    result = branch_reuse.find_reusable_branch(repo, 7, "main")
    assert result == "feat/issue-7-bbbb2222"
