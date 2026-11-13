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


def test_pipeline_id_last_comment_wins():
    comments = _comments(
        "<!-- pipeline-branch: feat/issue-42-aaaaaaaa -->",
        "other",
        "<!-- pipeline-branch: feat/issue-42-bbbbbbbb -->",
    )
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=comments):
        ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-bbbbbbbb"


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


def test_build_context_reuses_pipeline_id_from_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-1: find_reusable_branch returns a branch -> pipeline_id and branch match it."""
    import issuesmith.branch_reuse as br_mod

    repo = _setup_cross_repo_search(tmp_path, monkeypatch)
    _add_branch_with_commit(repo, "feat/issue-7-aaaa1111", "add feature X")
    br_mod.record_base(repo, "feat/issue-7-aaaa1111", "main")

    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "issue_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "```\n\n## Purpose\ntest"
    )
    ctx = build_context(7, body=body)
    assert ctx["pipeline_id"] == "issue-7-aaaa1111"
    assert ctx["branch"] == "feat/issue-7-aaaa1111"


def test_build_context_new_uuid_when_no_reusable_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-2: no reusable branch (wrong base) -> new uuid pipeline_id."""
    import issuesmith.branch_reuse as br_mod

    repo = _setup_cross_repo_search(tmp_path, monkeypatch)
    _add_branch_with_commit(repo, "feat/issue-7-aaaa1111", "some work")
    br_mod.record_base(repo, "feat/issue-7-aaaa1111", "develop")

    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "issue_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "```\n\n## Purpose\ntest"
    )
    ctx = build_context(7, body=body)
    assert ctx["pipeline_id"] != "issue-7-aaaa1111"
    assert ctx["pipeline_id"].startswith("issue-7-")


def test_build_context_previous_commits_populated_on_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3: previous_commits is non-empty and contains commit subjects on reuse."""
    import issuesmith.branch_reuse as br_mod

    repo = _setup_cross_repo_search(tmp_path, monkeypatch)
    _add_branch_with_commit(repo, "feat/issue-7-aaaa1111", "add feature X")
    br_mod.record_base(repo, "feat/issue-7-aaaa1111", "main")

    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "issue_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "```\n\n## Purpose\ntest"
    )
    ctx = build_context(7, body=body)
    assert ctx["previous_commits"] != ""
    assert "add feature X" in ctx["previous_commits"]
    line = ctx["previous_commits"].splitlines()[0]
    sha, subject = line.split(" ", 1)
    assert len(sha) == 7
    assert subject == "add feature X"


def test_build_context_previous_commits_empty_on_new_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: previous_commits is empty string when new uuid is generated."""
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: []
    )
    ctx = build_context(42, body="# Title")
    assert ctx["previous_commits"] == ""


def test_build_context_previous_commits_empty_on_comment_restore(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-3: previous_commits is empty string when pipeline_id is restored from comment."""
    comments = [{"body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->"}]
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: comments
    )
    ctx = build_context(42, body="# Title")
    assert ctx["previous_commits"] == ""
    assert ctx["pipeline_id"] == "issue-42-deadbeef"


def test_build_context_comment_takes_priority_over_branch_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3b: pipeline-branch comment present -> find_reusable_branch not called."""
    import issuesmith.branch_reuse as br_mod

    called = []

    def spy_find(*args, **kwargs):
        called.append(args)
        return br_mod.find_reusable_branch(*args, **kwargs)

    monkeypatch.setattr("issuesmith.context_hook.find_reusable_branch", spy_find)
    comments = [{"body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->"}]
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: comments
    )
    ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-deadbeef"
    assert called == []


# ===========================================================================
# AC-1: full comment list (>30) - pipeline-branch marker at position 35
# ===========================================================================


def test_pipeline_id_marker_found_among_40_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-1: 40 comments with marker at position 35 (beyond old 30-cap) is picked up."""
    comments = [{"body": "other"} for _ in range(34)]
    comments.append({"body": "<!-- pipeline-branch: feat/issue-42-ac1f1234 -->"})
    comments.extend([{"body": "other"} for _ in range(5)])
    assert len(comments) == 40
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: comments
    )
    ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-ac1f1234"
    assert ctx["reuse_source"] == "comment"


def test_pipeline_id_two_markers_last_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-1: when both position 5 and 35 have markers, position 35 wins."""
    comments = [{"body": "other"} for _ in range(4)]
    comments.append({"body": "<!-- pipeline-branch: feat/issue-42-f1f1f1f1 -->"})
    comments.extend([{"body": "other"} for _ in range(29)])
    comments.append({"body": "<!-- pipeline-branch: feat/issue-42-abcd1234 -->"})
    comments.extend([{"body": "other"} for _ in range(4)])
    assert len(comments) == 39
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: comments
    )
    ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-abcd1234"
    assert ctx["reuse_source"] == "comment"


# ===========================================================================
# AC-1b: _fetch_issue_comments_from_api fallback behaviour
# ===========================================================================


def test_fallback_comments_restore_pipeline_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-1b: when _fetch_issue_comments_from_api returns marker comments, build_context uses them."""
    comments = [{"body": "<!-- pipeline-branch: feat/issue-42-fa11bacc -->"}]
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: comments
    )
    ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-fa11bacc"
    assert ctx["reuse_source"] == "comment"


def test_build_context_falls_back_to_new_uuid_when_both_lookups_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-1b: when _fetch_issue_comments_from_api returns [] (both failed), new uuid is used."""
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: []
    )
    ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"].startswith("issue-42-")
    assert len(ctx["pipeline_id"]) == len("issue-42-") + 8
    assert ctx["reuse_source"] == "none"


# ===========================================================================
# AC-3a: reuse_source values for all four cases
# ===========================================================================


def test_reuse_source_comment(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-3a: pipeline-branch comment → reuse_source='comment'."""
    comments = [{"body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->"}]
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: comments
    )
    ctx = build_context(42, body="# Title")
    assert ctx["reuse_source"] == "comment"


def test_reuse_source_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-3a: no comments, no reusable branch → reuse_source='none'."""
    monkeypatch.setattr(
        "issuesmith.context_hook._fetch_issue_comments_from_api", lambda *_: []
    )
    ctx = build_context(42, body="# Title")
    assert ctx["reuse_source"] == "none"


def test_reuse_source_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-3a: find_reusable_branch returns recorded branch → reuse_source='recorded'."""
    import issuesmith.branch_reuse as br_mod

    repo = _setup_cross_repo_search(tmp_path, monkeypatch)
    _add_branch_with_commit(repo, "feat/issue-7-aaaa1111", "add feature X")
    br_mod.record_base(repo, "feat/issue-7-aaaa1111", "main")

    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "issue_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "```\n\n## Purpose\ntest"
    )
    ctx = build_context(7, body=body)
    assert ctx["reuse_source"] == "recorded"


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
