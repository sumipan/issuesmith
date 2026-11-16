"""tests/test_publish_diff_gates.py — publish post-commit diff gates (#3065 / #3221)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.ops.publish import PublishResult, _ensure_rebased, publish

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

# Measured fixture (#3216 / #3221): two-dot diff where only base advanced (reverse version)
_BASE_ADVANCED_TWO_DOT_DIFF = """\
diff --git a/pyproject.toml b/pyproject.toml
index aaaaaaa..bbbbbbb 100644
--- a/pyproject.toml
+++ b/pyproject.toml
@@ -1,5 +1,5 @@
-version = "0.28.0"
+version = "0.27.0"
"""


def test_ensure_rebased_returns_conflict():
    worktree = Path("/tmp/fake-wt")
    fetch = MagicMock(returncode=0, stdout="", stderr="")
    not_ancestor = MagicMock(returncode=1, stdout="", stderr="")
    empty_status = MagicMock(returncode=0, stdout="", stderr="")
    rebase_fail = MagicMock(returncode=1, stdout="", stderr="conflict")
    unmerged = MagicMock(returncode=0, stdout="src/foo.py\n", stderr="")
    abort = MagicMock(returncode=0, stdout="", stderr="")

    def _run(wt, *args, check=True):
        if args[:2] == ("fetch", "origin"):
            return fetch
        if args[:2] == ("merge-base", "--is-ancestor"):
            return not_ancestor
        if args[0] == "status":
            return empty_status
        if args == ("rebase", "origin/main"):
            return rebase_fail
        if args[:3] == ("diff", "--name-only", "--diff-filter=U"):
            return unmerged
        if args == ("rebase", "--abort"):
            return abort
        raise AssertionError(f"unexpected git args: {args}")

    with patch("issuesmith.ops.publish._run_git", side_effect=_run):
        result = _ensure_rebased(worktree, "main")
    assert result is not None
    assert result.status == "REBASE_CONFLICT"
    assert result.exit_code == 1
    assert "src/foo.py" in result.stderr


def test_publish_stops_on_rebase_conflict_before_gates():
    conflict = PublishResult(status="REBASE_CONFLICT", exit_code=1)
    with (
        patch("issuesmith.ops.publish._commit_if_needed") as commit,
        patch("issuesmith.ops.publish._ensure_rebased", return_value=conflict) as rebase,
        patch("issuesmith.ops.publish._check_commit_diff_gates") as gates,
        patch("issuesmith.ops.publish._maybe_bump_version") as bump,
    ):
        result = publish(
            issue_number=3221,
            branch="feat/x",
            base_branch="main",
            worktree=Path("/tmp/fake-wt"),
            repo="sumipan/issuesmith",
            issue_repo="sumipan/nexus",
        )
    commit.assert_called_once()
    rebase.assert_called_once_with(Path("/tmp/fake-wt"), "main", None)
    gates.assert_not_called()
    bump.assert_not_called()
    assert result.status == "REBASE_CONFLICT"
    assert result.exit_code == 1


def test_publish_stops_on_gate_failure_before_bump():
    """Gate stops after _commit_if_needed and before _maybe_bump_version; no bump."""
    with (
        patch("issuesmith.ops.publish._commit_if_needed") as commit,
        patch("issuesmith.ops.publish._ensure_rebased", return_value=None) as rebase,
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
    rebase.assert_called_once()
    gates.assert_called_once()
    bump.assert_not_called()
    ahead.assert_not_called()
    assert result.status == "P3_GATE_FAILED"
    assert result.exit_code == 1
