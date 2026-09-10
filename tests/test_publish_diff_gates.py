"""tests/test_publish_diff_gates.py — publish の commit 後 diff ゲート (#3065)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.ops.publish import _check_commit_diff_gates, publish


_EXACT_DIFF = """\
diff --git a/tests/test_ghdag_version.py b/tests/test_ghdag_version.py
index a962678..ec9ef1f 100644
--- a/tests/test_ghdag_version.py
+++ b/tests/test_ghdag_version.py
@@ -63,5 +63,5 @@
-    assert project["version"] == "0.25.1"
+    assert project["version"] == "0.26.0"
"""

_VERSION_LINE_DIFF = """\
diff --git a/pyproject.toml b/pyproject.toml
index 1111111..2222222 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,5 +1,5 @@
-version = "0.26.0"
+version = "0.27.0"
"""


def test_check_commit_diff_gates_rejects_exact_assert():
    worktree = Path("/tmp/fake-wt")
    mock_result = MagicMock()
    mock_result.stdout = _EXACT_DIFF
    with patch("issuesmith.ops.publish._run_git", return_value=mock_result) as run_git:
        result = _check_commit_diff_gates(worktree, "main")
    run_git.assert_called_once_with(worktree, "diff", "origin/main..HEAD")
    assert result is not None
    assert result.status == "P3_GATE_FAILED"
    assert result.exit_code == 1
    assert "cp1.test_version_exact_assert" in result.stderr


def test_check_commit_diff_gates_rejects_version_line():
    worktree = Path("/tmp/fake-wt")
    mock_result = MagicMock()
    mock_result.stdout = _VERSION_LINE_DIFF
    with patch("issuesmith.ops.publish._run_git", return_value=mock_result):
        result = _check_commit_diff_gates(worktree, "main")
    assert result is not None
    assert result.status == "P3_GATE_FAILED"
    assert "cp1.version_line_in_diff" in result.stderr


def test_check_commit_diff_gates_passes_clean_diff():
    worktree = Path("/tmp/fake-wt")
    mock_result = MagicMock()
    mock_result.stdout = (
        "diff --git a/src/pkg/foo.py b/src/pkg/foo.py\n"
        "--- a/src/pkg/foo.py\n"
        "+++ b/src/pkg/foo.py\n"
        "+x = 1\n"
    )
    with patch("issuesmith.ops.publish._run_git", return_value=mock_result):
        assert _check_commit_diff_gates(worktree, "main") is None


def test_publish_stops_on_gate_failure_before_bump():
    """_commit_if_needed 後・_maybe_bump_version 前にゲートが止まり bump しない."""
    gate_fail = MagicMock()
    gate_fail.status = "P3_GATE_FAILED"
    gate_fail.stderr = "blocked"
    gate_fail.exit_code = 1
    # NamedTuple-compatible: use real return from helper via patch
    from issuesmith.ops.publish import PublishResult

    with (
        patch("issuesmith.ops.publish._commit_if_needed") as commit,
        patch(
            "issuesmith.ops.publish._check_commit_diff_gates",
            return_value=PublishResult(status="P3_GATE_FAILED", stderr="blocked", exit_code=1),
        ) as gates,
        patch("issuesmith.ops.publish._maybe_bump_version") as bump,
        patch("issuesmith.ops.publish._ahead_commit_count") as ahead,
    ):
        result = publish(
            issue_number=3065,
            branch="feat/x",
            base_branch="main",
            worktree=Path("/tmp/fake-wt"),
            repo="sumipan/issuesmith",
            issue_repo="sumipan/nexus",
        )
    commit.assert_called_once()
    gates.assert_called_once()
    bump.assert_not_called()
    ahead.assert_not_called()
    assert result.status == "P3_GATE_FAILED"
    assert result.exit_code == 1
