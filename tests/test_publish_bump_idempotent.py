"""publish re-run after a bump commit was pushed: skip the bump, gate below it, PR_LIST_FAILED (#3767 / #3794)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.ops.publish import (
    _bump_commits_on_top,
    _check_commit_diff_gates,
    _maybe_bump_version,
    publish,
)

_BUMP_ONLY_DIFF = """\
diff --git a/pyproject.toml b/pyproject.toml
index 1111111..2222222 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,5 +1,5 @@
-version = "0.26.0"
+version = "0.27.0"
"""


def _git_router(log_subjects: list[str], diffs: dict[str, str]):
    """Fake _run_git: `log` returns subjects, `diff` returns the diff keyed by the range argument."""

    def _run(worktree, *args, check=True):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        if args[0] == "log":
            result.stdout = "\n".join(log_subjects) + ("\n" if log_subjects else "")
        elif args[0] == "diff":
            result.stdout = diffs.get(args[-1], "")
        else:
            result.stdout = ""
        return result

    return _run


def test_bump_commits_on_top_counts_only_leading_bump_subjects():
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(
        ["chore: bump version to 0.79.0 (Y: B1)", "test: rewrite docstrings", "fix: allow Write"], {}
    )):
        assert _bump_commits_on_top(Path("/tmp/wt"), "main") == 1
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(
        ["fix: allow Write", "chore: bump version to 0.79.0 (Y: B1)"], {}
    )):
        assert _bump_commits_on_top(Path("/tmp/wt"), "main") == 0
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router([], {})):
        assert _bump_commits_on_top(Path("/tmp/wt"), "main") == 0


def test_diff_gate_inspects_below_our_bump_commit():
    router = _git_router(
        ["chore: bump version to 0.79.0 (Y: B1)", "fix: allow Write"],
        {"origin/main...HEAD": _BUMP_ONLY_DIFF, "origin/main...HEAD~1": "diff --git a/src/x.py b/src/x.py\n+x = 1\n"},
    )
    with patch("issuesmith.ops.publish._run_git", side_effect=router):
        assert _check_commit_diff_gates(Path("/tmp/wt"), "main") is None


def test_diff_gate_still_rejects_llm_version_edit_without_bump_commit():
    router = _git_router(["fix: bump by hand"], {"origin/main...HEAD": _BUMP_ONLY_DIFF})
    with patch("issuesmith.ops.publish._run_git", side_effect=router):
        result = _check_commit_diff_gates(Path("/tmp/wt"), "main")
    assert result is not None and result.status == "P3_GATE_FAILED"


def test_maybe_bump_version_skips_when_head_is_bump_commit(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('version = "0.79.0"\n', encoding="utf-8")
    router = _git_router(["chore: bump version to 0.79.0 (Y: B1)", "fix: x"], {})
    with patch("issuesmith.ops.publish._run_git", side_effect=router), \
         patch("issuesmith.ops.publish._run_version_bump") as run_bump:
        assert _maybe_bump_version(tmp_path, "main", "sumipan/ghdag", "sumipan/nexus") is None
    run_bump.assert_not_called()


def test_maybe_bump_version_runs_when_no_bump_commit(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('version = "0.78.0"\n', encoding="utf-8")
    router = _git_router(["fix: x"], {})
    ok = MagicMock(returncode=0, stdout="", stderr="")
    with patch("issuesmith.ops.publish._run_git", side_effect=router), \
         patch("issuesmith.ops.publish._run_version_bump", return_value=ok) as run_bump:
        assert _maybe_bump_version(tmp_path, "main", "sumipan/ghdag", "sumipan/nexus") is None
    run_bump.assert_called_once()


def test_publish_reports_pr_list_failure_instead_of_crashing(tmp_path: Path):
    client = MagicMock()
    client.pr_list.side_effect = RuntimeError("GitHub API GET .../pulls failed (403): API rate limit exceeded")
    with patch("issuesmith.ops.publish._commit_if_needed"), \
         patch("issuesmith.ops.publish._ensure_rebased", return_value=None), \
         patch("issuesmith.ops.publish._check_commit_diff_gates", return_value=None), \
         patch("issuesmith.ops.publish._maybe_bump_version", return_value=None), \
         patch("issuesmith.ops.publish._ahead_commit_count", return_value=2), \
         patch("issuesmith.ops.publish._push_branch", return_value=None), \
         patch("issuesmith.ops.publish.get_forge", return_value=client):
        result = publish(
            issue_number=3572, branch="feat/issue-3572-b67bdaa4", base_branch="main",
            worktree=tmp_path, repo="sumipan/ghdag", issue_repo="sumipan/nexus",
        )
    assert result.status == "PR_LIST_FAILED"
    assert "rate limit" in result.stderr
    assert result.exit_code == 1
    client.pr_create.assert_not_called()
