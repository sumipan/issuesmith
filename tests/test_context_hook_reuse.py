"""context_hook: pipeline id reuse from comments and branches."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from issuesmith.context_hook import (
    build_context,
)
from tests import test_context_hook as _base

_no_real_branch_search = _base._no_real_branch_search


def _comments(*bodies: str) -> list[dict]:
    return [{"body": b, "author": {"login": "bot"}, "createdAt": ""} for b in bodies]


def test_pipeline_id_restored_from_comment():
    comments = _comments("<!-- pipeline-branch: feat/issue-42-deadbeef -->")
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=comments):
        ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-deadbeef"
    assert ctx["branch"] == "feat/issue-42-deadbeef"




def test_pipeline_id_new_when_no_comments():
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"].startswith("issue-42-")
    assert len(ctx["pipeline_id"]) == len("issue-42-") + 8


# ===========================================================================
# AC-1, AC-3, AC-3b, AC-3c: branch reuse integration tests
# ===========================================================================


def _git_init_repo_for_hook(path: Path) -> None:
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


def _add_branch_with_commit(repo: Path, branch: str, subject: str, filename: str = "f.txt") -> None:
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", branch],
        check=True,
        capture_output=True,
    )
    f = repo / filename
    f.write_text(subject + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(f)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", subject],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )


def _setup_cross_repo_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repo_name: str = "issuesmith",
) -> Path:
    """Create a fake external clone at the path build_context will search, return its path."""
    import issuesmith.branch_reuse as br_mod

    external_root = tmp_path / "ext"
    repo = external_root / repo_name
    _git_init_repo_for_hook(repo)
    monkeypatch.setattr("issuesmith.context_hook._REPO_ROOT", str(tmp_path))
    monkeypatch.setattr("issuesmith.context_hook._EXTERNAL_REL", "ext")
    monkeypatch.setattr("issuesmith.context_hook.find_reusable_branch", br_mod.find_reusable_branch)
    monkeypatch.setattr("issuesmith.context_hook._prev_commits", br_mod.previous_commits)
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: []
    )
    return repo














# ===========================================================================
# AC-1: full comment list (>30) - pipeline-branch marker at position 35
# ===========================================================================






# ===========================================================================
# AC-1b: _fetch_issue_comments_from_api fallback behaviour
# ===========================================================================






# ===========================================================================
# AC-3a: reuse_source values for all four cases
# ===========================================================================








def test_reuse_source_unrecorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3a: find_reusable_branch returns unrecorded branch → reuse_source='unrecorded'."""
    repo = _setup_cross_repo_search(tmp_path, monkeypatch)
    _add_branch_with_commit(repo, "feat/issue-7-bbbb2222", "add feature Y")
    # No record_base call → unrecorded

    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "issue_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "```\n\n## Purpose\ntest"
    )
    ctx = build_context(7, body=body)
    assert ctx["reuse_source"] == "unrecorded"
    assert ctx["pipeline_id"] == "issue-7-bbbb2222"
