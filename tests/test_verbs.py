"""Tests for issuesmith.verbs public API (#3506)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith import verbs
from issuesmith.verbs import (
    cleanup_branches,
    cleanup_worktrees,
    close_issue,
    find_pr,
    merge_pr,
    merge_state,
    prepare_worktree,
    publish_branch,
)

# ---------------------------------------------------------------------------
# prepare_worktree
# ---------------------------------------------------------------------------


def test_prepare_worktree_calls_underlying_prepare(tmp_path: Path) -> None:
    with (
        patch("issuesmith.verbs.worktree.resolve_base_ref") as mock_resolve,
        patch("issuesmith.verbs.worktree._prepare_worktree") as mock_prepare,
    ):
        mock_resolve.return_value = "origin/main"
        prepare_worktree(tmp_path, "main", "feat/test", tmp_path / "wt")
        mock_resolve.assert_called_once_with(tmp_path, "main")
        mock_prepare.assert_called_once_with(tmp_path, tmp_path / "wt", "feat/test", "origin/main")


def test_prepare_worktree_uses_external_dir_when_provided(tmp_path: Path) -> None:
    external = tmp_path / "external"
    with (
        patch("issuesmith.verbs.worktree.resolve_base_ref") as mock_resolve,
        patch("issuesmith.verbs.worktree._prepare_worktree") as mock_prepare,
    ):
        mock_resolve.return_value = "main"
        prepare_worktree(tmp_path, "main", "feat/x", tmp_path / "wt", external_dir=external)
        mock_resolve.assert_called_once_with(external, "main")
        mock_prepare.assert_called_once_with(external, tmp_path / "wt", "feat/x", "main")


# ---------------------------------------------------------------------------
# publish_branch
# ---------------------------------------------------------------------------


def test_publish_branch_delegates_to_push_branch_for_origin(tmp_path: Path) -> None:
    with patch("issuesmith.verbs.publish._push_branch") as mock_push:
        mock_push.return_value = None  # success
        publish_branch(tmp_path, "feat/test")
        mock_push.assert_called_once_with(tmp_path, "feat/test")


def test_publish_branch_raises_on_push_failure(tmp_path: Path) -> None:
    from issuesmith.ops.publish import PublishResult

    with patch("issuesmith.verbs.publish._push_branch") as mock_push:
        mock_push.return_value = PublishResult(
            status="PUSH_DIVERGED", stderr="rejected", exit_code=1
        )
        with pytest.raises(RuntimeError, match="push failed"):
            publish_branch(tmp_path, "feat/test")


def test_publish_branch_uses_subprocess_for_non_origin_remote(tmp_path: Path) -> None:
    with patch("issuesmith.verbs.publish.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        publish_branch(tmp_path, "feat/test", remote="upstream")
        cmd = mock_run.call_args[0][0]
        assert "upstream" in cmd
        assert "feat/test" in cmd


def test_publish_branch_raises_on_non_origin_push_failure(tmp_path: Path) -> None:
    with patch("issuesmith.verbs.publish.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stderr="error msg", stdout="")
        with pytest.raises(RuntimeError, match="git push to upstream failed"):
            publish_branch(tmp_path, "feat/test", remote="upstream")


# ---------------------------------------------------------------------------
# find_pr
# ---------------------------------------------------------------------------


def test_find_pr_returns_number_when_found() -> None:
    client = MagicMock()
    with patch("issuesmith.verbs.merge._m1_find_pr") as mock_find:
        mock_find.return_value = (42, "open", "branch")
        result = find_pr(client, "owner/repo", "feat/x", 100)
        assert result == 42
        mock_find.assert_called_once_with(client, "owner/repo", "feat/x", 100)


def test_find_pr_returns_none_when_not_found() -> None:
    client = MagicMock()
    with patch("issuesmith.verbs.merge._m1_find_pr") as mock_find:
        mock_find.return_value = (None, "", "")
        result = find_pr(client, "owner/repo", "feat/x", 100)
        assert result is None


# ---------------------------------------------------------------------------
# merge_state
# ---------------------------------------------------------------------------


def test_merge_state_delegates_to_get_merge_state() -> None:
    client = MagicMock()
    expected = {"mergeStateStatus": "CLEAN", "mergeable": "MERGEABLE", "state": "open", "headRefName": "feat/x"}
    with patch("issuesmith.verbs.merge._get_merge_state") as mock_state:
        mock_state.return_value = expected
        result = merge_state(client, "owner/repo", 42)
        assert result == expected
        mock_state.assert_called_once_with(client, "owner/repo", 42)


# ---------------------------------------------------------------------------
# merge_pr
# ---------------------------------------------------------------------------


def test_merge_pr_calls_client_pr_merge() -> None:
    client = MagicMock()
    merge_pr(client, "owner/repo", 42)
    client.pr_merge.assert_called_once_with(42, method="merge", delete_branch=True, repo="owner/repo")


def test_merge_pr_respects_delete_branch_flag() -> None:
    client = MagicMock()
    merge_pr(client, "owner/repo", 42, delete_branch=False)
    client.pr_merge.assert_called_once_with(42, method="merge", delete_branch=False, repo="owner/repo")


def test_merge_pr_passes_none_repo_for_empty_string() -> None:
    client = MagicMock()
    merge_pr(client, "", 42)
    client.pr_merge.assert_called_once_with(42, method="merge", delete_branch=True, repo=None)


# ---------------------------------------------------------------------------
# cleanup_branches
# ---------------------------------------------------------------------------


def test_cleanup_branches_delegates_to_m2_finalize(tmp_path: Path) -> None:
    with patch("issuesmith.verbs.finalize._cleanup_branches") as mock_cleanup:
        cleanup_branches(tmp_path, "1234")
        mock_cleanup.assert_called_once_with(tmp_path, "1234", external=False)


def test_cleanup_branches_passes_external_flag(tmp_path: Path) -> None:
    with patch("issuesmith.verbs.finalize._cleanup_branches") as mock_cleanup:
        cleanup_branches(tmp_path, "1234", external=True)
        mock_cleanup.assert_called_once_with(tmp_path, "1234", external=True)


# ---------------------------------------------------------------------------
# cleanup_worktrees
# ---------------------------------------------------------------------------


def test_cleanup_worktrees_removes_matching_worktrees(tmp_path: Path) -> None:
    worktree_path = tmp_path / ".claude" / "worktrees" / "issue-99-abc"
    with (
        patch("issuesmith.verbs.finalize._list_worktrees") as mock_list,
        patch("issuesmith.verbs.finalize._remove_worktree") as mock_remove,
        patch("issuesmith.verbs.finalize._cleanup_branches"),
    ):
        mock_list.return_value = [worktree_path]
        cleanup_worktrees(tmp_path, "99")
        mock_remove.assert_called_once_with(tmp_path, worktree_path)


def test_cleanup_worktrees_skips_non_matching(tmp_path: Path) -> None:
    other_path = tmp_path / ".claude" / "worktrees" / "issue-100-abc"
    with (
        patch("issuesmith.verbs.finalize._list_worktrees") as mock_list,
        patch("issuesmith.verbs.finalize._remove_worktree") as mock_remove,
        patch("issuesmith.verbs.finalize._cleanup_branches"),
    ):
        mock_list.return_value = [other_path]
        cleanup_worktrees(tmp_path, "99")
        mock_remove.assert_not_called()


def test_cleanup_worktrees_handles_cross_repo(tmp_path: Path) -> None:
    target_clone = tmp_path / "target"
    (target_clone / ".git").mkdir(parents=True)
    with (
        patch("issuesmith.verbs.finalize._list_worktrees") as mock_list,
        patch("issuesmith.verbs.finalize._remove_worktree"),
        patch("issuesmith.verbs.finalize._cleanup_branches"),
    ):
        mock_list.return_value = []
        cleanup_worktrees(tmp_path, "99", is_cross_repo=True, target_clone_path=target_clone)
        # Called twice: once for repo_cwd, once for target
        assert mock_list.call_count == 2


# ---------------------------------------------------------------------------
# close_issue
# ---------------------------------------------------------------------------


def test_close_issue_delegates_to_close_issue_if_open() -> None:
    client = MagicMock()
    with patch("issuesmith.verbs.finalize._close_issue_if_open") as mock_close:
        close_issue(client, 42)
        mock_close.assert_called_once_with(client, 42)


# ---------------------------------------------------------------------------
# Module-level smoke: all names are importable from issuesmith.verbs
# ---------------------------------------------------------------------------


def test_all_verbs_are_importable() -> None:
    for name in verbs.__all__:
        assert hasattr(verbs, name), f"missing: {name}"
