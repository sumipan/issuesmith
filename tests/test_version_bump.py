"""version_bump B1 detection: Conventional Commits only, bump commits excluded (#4508)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.ops.version_bump import decide_bump

_STALE_BUMP_SUBJECT = (
    "chore: bump version to 0.2.0 (Y: B1 — commit message indicates breaking change)"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit(repo: Path, rel: str, content: str, message: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    _commit(repo, "pyproject.toml", 'version = "0.1.0"\n', "chore: init")
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 1\n", "feat: add api")
    return repo


def _point_origin_main_at_head(repo: Path) -> None:
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def test_stale_local_main_with_bump_commit_is_z_against_origin_main(tmp_path: Path):
    repo = _init_repo(tmp_path)
    # Upstream progressed (incl. a publish bump commit) but local main stayed behind.
    _git(repo, "checkout", "-q", "-b", "work")
    _commit(repo, "pyproject.toml", 'version = "0.2.0"\n', _STALE_BUMP_SUBJECT)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 2\n", "fix: upstream change")
    _point_origin_main_at_head(repo)
    # The PR itself: internal-only change, no public API change.
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", "fix: tweak internals")

    decision = decide_bump(repo, "origin/main")

    assert decision.bump_type == "Z", decision.reason


def test_bump_commit_subject_alone_is_not_b1(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _point_origin_main_at_head(repo)
    _commit(repo, "pyproject.toml", 'version = "0.2.0"\n', _STALE_BUMP_SUBJECT)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", "fix: tweak internals")

    decision = decide_bump(repo, "origin/main")

    assert decision.bump_type == "Z", decision.reason


@pytest.mark.parametrize(
    "message",
    [
        "feat!: add feature",
        "fix!: handle edge case",
        "chore(scope)!: rename internal key",
        "fix: drop old API\n\nBREAKING CHANGE: drop old API",
        "fix: remove flag\n\nBREAKING-CHANGE: remove flag",
    ],
)
def test_conventional_commits_breaking_markers_are_b1(tmp_path: Path, message: str):
    repo = _init_repo(tmp_path)
    _point_origin_main_at_head(repo)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", message)

    decision = decide_bump(repo, "origin/main")

    assert (decision.bump_type, decision.reason_code) == ("Y", "B1")


@pytest.mark.parametrize(
    "message",
    [
        "fix: avoid breaking the parser on empty input",
        "docs: note breaking change policy",
        "fix: handle 'wow!: text' in body\n\nnot a breaking change at all",
    ],
)
def test_prose_mentioning_breaking_is_not_b1(tmp_path: Path, message: str):
    repo = _init_repo(tmp_path)
    _point_origin_main_at_head(repo)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", message)

    decision = decide_bump(repo, "origin/main")

    assert decision.bump_type == "Z", decision.reason
