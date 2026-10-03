"""publish re-run after a bump commit was pushed: skip the bump, gate below it, PR_LIST_FAILED (#3767 / #3794)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

from issuesmith.ops.publish import (
    _bump_versions_in_range,
    _check_commit_diff_gates,
    _maybe_bump_version,
    _strip_bump_version_lines,
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

_ISSUE_3922_LOG = [
    "fix: b",
    "fix: a",
    "chore: bump version to 0.105.0 (Y: B1)",
    "feat: impl",
]

_ISSUE_3922_CUMULATIVE_DIFF = """\
diff --git a/pyproject.toml b/pyproject.toml
index 1111111..2222222 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,5 +1,5 @@
-version = "0.104.0"
+version = "0.105.0"
diff --git a/src/pkg/foo.py b/src/pkg/foo.py
--- a/src/pkg/foo.py
+++ b/src/pkg/foo.py
@@ -1 +1,2 @@
+x = 1
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


def test_bump_versions_in_range_collects_bump_subjects_anywhere_in_range():
    subjects = ["fix: b", "fix: a", "chore: bump version to 0.105.0 (Y: B1)", "feat: impl"]
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(subjects, {})):
        assert _bump_versions_in_range(Path("/tmp/wt"), "main") == ["0.105.0"]
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(
        ["chore: bump version to 0.79.0 (Y: B1)", "test: rewrite docstrings", "fix: allow Write"], {}
    )):
        assert _bump_versions_in_range(Path("/tmp/wt"), "main") == ["0.79.0"]
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(
        ["fix: allow Write", "chore: bump version to 0.79.0 (Y: B1)"], {}
    )):
        assert _bump_versions_in_range(Path("/tmp/wt"), "main") == ["0.79.0"]
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router([], {})):
        assert _bump_versions_in_range(Path("/tmp/wt"), "main") == []
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(["", "fix: x"], {})):
        assert _bump_versions_in_range(Path("/tmp/wt"), "main") == []


def test_strip_bump_version_lines_removes_matching_pyproject_version_hunks():
    diff = _ISSUE_3922_CUMULATIVE_DIFF
    stripped = _strip_bump_version_lines(diff, ["0.105.0"])
    assert "-version = " not in stripped
    assert '+version = "0.105.0"' not in stripped
    assert "src/pkg/foo.py" in stripped
    assert '+version = "0.106.0"' in _strip_bump_version_lines(
        diff.replace('0.105.0', '0.106.0'), ["0.105.0"]
    )
    assert _strip_bump_version_lines(diff, []) == diff


def test_diff_gate_passes_issue_3922_layout_with_bump_not_at_head():
    router = _git_router(_ISSUE_3922_LOG, {"origin/main...HEAD": _ISSUE_3922_CUMULATIVE_DIFF})
    with patch("issuesmith.ops.publish._run_git", side_effect=router):
        assert _check_commit_diff_gates(Path("/tmp/wt"), "main") is None


def test_diff_gate_rejects_llm_version_edit_after_publish_bump():
    llm_diff = _ISSUE_3922_CUMULATIVE_DIFF.replace(
        '+version = "0.105.0"', '+version = "0.106.0"'
    )
    router = _git_router(_ISSUE_3922_LOG, {"origin/main...HEAD": llm_diff})
    with patch("issuesmith.ops.publish._run_git", side_effect=router):
        result = _check_commit_diff_gates(Path("/tmp/wt"), "main")
    assert result is not None and result.status == "P3_GATE_FAILED"
    assert "cp1.version_line_in_diff" in result.stderr


def test_diff_gate_passes_when_bump_at_head_using_cumulative_diff():
    bump_079_diff = _BUMP_ONLY_DIFF.replace("0.26.0", "0.78.0").replace("0.27.0", "0.79.0")
    cumulative = bump_079_diff + "\n" + (
        "diff --git a/src/x.py b/src/x.py\n"
        "--- a/src/x.py\n"
        "+++ b/src/x.py\n"
        "+x = 1\n"
    )
    router = _git_router(
        ["chore: bump version to 0.79.0 (Y: B1)", "fix: allow Write"],
        {"origin/main...HEAD": cumulative},
    )
    with patch("issuesmith.ops.publish._run_git", side_effect=router):
        assert _check_commit_diff_gates(Path("/tmp/wt"), "main") is None


def test_diff_gate_still_rejects_llm_version_edit_without_bump_commit():
    router = _git_router(["fix: bump by hand"], {"origin/main...HEAD": _BUMP_ONLY_DIFF})
    with patch("issuesmith.ops.publish._run_git", side_effect=router):
        result = _check_commit_diff_gates(Path("/tmp/wt"), "main")
    assert result is not None and result.status == "P3_GATE_FAILED"


def test_maybe_bump_version_skips_when_bump_commit_in_range(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('version = "0.79.0"\n', encoding="utf-8")
    router = _git_router(["chore: bump version to 0.79.0 (Y: B1)", "fix: x"], {})
    with patch("issuesmith.ops.publish._run_git", side_effect=router), \
         patch("issuesmith.ops.publish._run_version_bump") as run_bump:
        assert _maybe_bump_version(tmp_path, "main", "sumipan/ghdag", "sumipan/nexus") is None
    run_bump.assert_not_called()


def test_maybe_bump_version_skips_issue_3922_layout(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('version = "0.105.0"\n', encoding="utf-8")
    router = _git_router(_ISSUE_3922_LOG, {})
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
    # Compare against the fetched remote ref; local main may be stale (#4508).
    run_bump.assert_called_with(ANY, "origin/main")


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
