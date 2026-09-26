"""Fixture tests for issuesmith.steps.m1_merge (#3164).

PR / GraphQL fixtures were captured 2026-09-11 from live GitHub via
``ForgePort.api_request`` / GraphQL (CLAUDE.md §10):

  api_request("repos/sumipan/nexus/pulls?head=sumipan%3Afeat%2Fissue-3173-eb3c5291-diary&state=open")
  → success list (number=3183, body contains Refs #3173)

  api_request("repos/sumipan/nexus/pulls?head=nobody%3Afeat%2Fnonexistent-zzzz&state=open")
  → []

  api_request("repos/sumipan/nexus/pulls/3183")
  → merged=false, mergeable_state=unknown

  api_request("repos/sumipan/nexus/pulls/3181")
  → merged=true, state=closed

  GraphQL pullRequest(number:3183) (retry until computed)
  → mergeStateStatus=CLEAN, mergeable=MERGEABLE

  GraphQL BLOCKED: live BLOCKED PR was not found in sumipan/{nexus,issuesmith}
  on 2026-09-11; fixture uses the same envelope/fields as the live CLEAN
  capture with mergeStateStatus=BLOCKED (valid MergeStateStatus enum).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.steps import m1_merge as m1
from issuesmith.steps.base import StepContext

# --- Real API strings (trimmed to fields the step reads; values unchanged) ---

PR_LIST_SUCCESS_JSON = json.dumps(
    [
        {
            "number": 3183,
            "state": "open",
            "title": "Implement: Issue #3173",
            "body": "Auto-generated from P1/P2 result.\n\nRefs #3173",
            "draft": False,
            "head": {
                "ref": "feat/issue-3173-eb3c5291-diary",
                "label": "sumipan:feat/issue-3173-eb3c5291-diary",
            },
        }
    ],
)

PR_LIST_ABSENT_JSON = "[]"

PR_DETAIL_OPEN_JSON = json.dumps(
    {
        "number": 3183,
        "state": "open",
        "merged": False,
        "mergeable": None,
        "mergeable_state": "unknown",
        "title": "Implement: Issue #3173",
        "draft": False,
        "body": "Auto-generated from P1/P2 result.\n\nRefs #3173",
        "head": {
            "ref": "feat/issue-3173-eb3c5291-diary",
            "label": "sumipan:feat/issue-3173-eb3c5291-diary",
        },
    },
)

PR_DETAIL_MERGED_JSON = json.dumps(
    {
        "number": 3181,
        "state": "closed",
        "merged": True,
        "mergeable": None,
        "mergeable_state": "unknown",
        "title": "Implement: Issue #3128",
        "draft": False,
        "body": "Auto-generated from P1/P2 result.\n\nRefs #3128",
        "head": {
            "ref": "feat/issue-3128-52c4f66e",
            "label": "sumipan:feat/issue-3128-52c4f66e",
        },
    },
)

GQL_CLEAN_JSON = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequest": {
                    "mergeStateStatus": "CLEAN",
                    "mergeable": "MERGEABLE",
                    "state": "OPEN",
                    "headRefName": "feat/issue-3173-eb3c5291-diary",
                }
            }
        }
    }
)

GQL_BLOCKED_JSON = json.dumps(
    {
        "data": {
            "repository": {
                "pullRequest": {
                    "mergeStateStatus": "BLOCKED",
                    "mergeable": "MERGEABLE",
                    "state": "OPEN",
                    "headRefName": "feat/issue-3173-eb3c5291-diary",
                }
            }
        }
    }
)


def _ctx(**overrides: str) -> StepContext:
    base = {
        "issue_number": "3173",
        "base_branch": "main",
        "handler_name": "merge",
        "is_cross_repo": "false",
        "target_clone_path": "",
        "source": "",
        "workflow_name": "issuesmith",
        "m1_result_filename": "",
        "m1r_result_filename": "",
        "worktree_path": "/tmp/wt",
        "target_worktree_path": "/tmp/twt",
        "branch": "feat/issue-3173-eb3c5291-diary",
        "target_repo": "",
        "allow_paths": "- src/**",
        "issue_repo": "sumipan/nexus",
        "has_diary_changes": "false",
    }
    base.update(overrides)
    return StepContext(**base)


def test_find_pr_success_uses_real_list_string() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_SUCCESS_JSON)
    number, state, stage = m1._find_pr(
        client,
        "sumipan/nexus",
        "feat/issue-3173-eb3c5291-diary",
        3173,
    )
    assert number == 3183
    assert state == "open"
    assert stage == "branch"
    assert "head=" in client.api_request.call_args.args[0]


def test_find_pr_absent_uses_real_empty_list_string() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_ABSENT_JSON)
    number, state, stage = m1._find_pr(
        client, "sumipan/nexus", "feat/nonexistent-zzzz", 9999
    )
    assert number is None
    assert state == ""
    assert stage == ""


def test_already_merged_true_uses_real_merged_field() -> None:
    detail = json.loads(PR_DETAIL_MERGED_JSON)
    assert detail["merged"] is True
    client = MagicMock()
    client.api_request.return_value = detail
    assert m1._is_already_merged(client, "sumipan/nexus", 3181) is True


def test_already_merged_false_uses_real_open_detail() -> None:
    detail = json.loads(PR_DETAIL_OPEN_JSON)
    assert detail["merged"] is False
    client = MagicMock()
    client.api_request.return_value = detail
    assert m1._is_already_merged(client, "sumipan/nexus", 3183) is False


def test_merge_state_clean_parses_real_graphql_string() -> None:
    payload = json.loads(GQL_CLEAN_JSON)
    pr = payload["data"]["repository"]["pullRequest"]
    assert pr["mergeStateStatus"] == "CLEAN"
    assert pr["mergeable"] == "MERGEABLE"
    client = MagicMock()
    with patch.object(m1, "_graphql_merge_state", return_value=pr):
        got = m1._get_merge_state(client, "sumipan/nexus", 3183)
    assert got["mergeStateStatus"] == "CLEAN"
    assert got["headRefName"] == "feat/issue-3173-eb3c5291-diary"


def test_merge_state_blocked_parses_graphql_blocked_fixture() -> None:
    payload = json.loads(GQL_BLOCKED_JSON)
    pr = payload["data"]["repository"]["pullRequest"]
    assert pr["mergeStateStatus"] == "BLOCKED"
    client = MagicMock()
    with patch.object(m1, "_graphql_merge_state", return_value=pr):
        got = m1._get_merge_state(client, "sumipan/nexus", 3183)
    assert got["mergeStateStatus"] == "BLOCKED"


def test_merge_state_retries_on_blocked() -> None:
    blocked = json.loads(GQL_BLOCKED_JSON)["data"]["repository"]["pullRequest"]
    clean = json.loads(GQL_CLEAN_JSON)["data"]["repository"]["pullRequest"]
    client = MagicMock()
    with (
        patch.object(m1, "_graphql_merge_state", side_effect=[blocked, blocked, clean]),
        patch.object(m1, "time") as mock_time,
    ):
        mock_time.sleep = MagicMock()
        got = m1._poll_merge_state(client, "sumipan/nexus", 3183)
    assert got["mergeStateStatus"] == "CLEAN"
    assert mock_time.sleep.call_count == 2


def test_run_always_exit_0_merge_reported_when_pr_absent() -> None:
    client = MagicMock()
    client.api_request.return_value = []
    with patch.object(m1, "_github_client", return_value=client):
        result = m1.run(_ctx(branch="feat/missing"))
    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_REPORTED"


def test_run_always_exit_0_merge_reported_when_already_merged() -> None:
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_MERGED_JSON),
    ]
    with patch.object(m1, "_github_client", return_value=client):
        result = m1.run(_ctx())
    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_REPORTED"


def test_run_always_exit_0_merge_reported_on_blocked_skip() -> None:
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_OPEN_JSON),
    ]
    blocked = json.loads(GQL_BLOCKED_JSON)["data"]["repository"]["pullRequest"]
    with (
        patch.object(m1, "_github_client", return_value=client),
        patch.object(m1, "_poll_merge_state", return_value=blocked),
        patch.object(m1, "_local_merge_verify", return_value=("SKIPPED", False)),
        patch.object(m1, "_m2_gate_preflight", return_value=[]),
        patch.object(m1, "time") as mock_time,
    ):
        mock_time.sleep = MagicMock()
        result = m1.run(_ctx())
    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_REPORTED"
    client.pr_merge.assert_not_called()


def test_run_merge_clean_still_reports_merge_reported() -> None:
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_OPEN_JSON),
    ]
    clean = json.loads(GQL_CLEAN_JSON)["data"]["repository"]["pullRequest"]
    with (
        patch.object(m1, "_github_client", return_value=client),
        patch.object(m1, "_poll_merge_state", return_value=clean),
        patch.object(m1, "_m2_gate_preflight", return_value=[]),
        patch.object(m1, "_post_merge_pytest", return_value=0),
    ):
        result = m1.run(_ctx())
    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_REPORTED"
    client.pr_merge.assert_called_once()
    client.issue_update.assert_not_called()


def test_post_merge_test_failure_adds_merge_running() -> None:
    """After PR merge succeeds, post_merge_test-only failure still adds merge-running (#3221 AC-6)."""
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_OPEN_JSON),
    ]
    clean = json.loads(GQL_CLEAN_JSON)["data"]["repository"]["pullRequest"]
    with (
        patch.object(m1, "_github_client", return_value=client),
        patch.object(m1, "_poll_merge_state", return_value=clean),
        patch.object(m1, "_m2_gate_preflight", return_value=[]),
        patch.object(m1, "_post_merge_pytest", return_value=1),
    ):
        result = m1.run(_ctx())
    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_REPORTED"
    client.pr_merge.assert_called_once()
    client.issue_update.assert_called_once_with(
        3173, labels_add=["issuesmith:merge-running"]
    )


def test_companion_merge_only_when_has_diary_changes() -> None:
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_OPEN_JSON),
        [],  # companion list empty when has_diary_changes path runs
    ]
    clean = json.loads(GQL_CLEAN_JSON)["data"]["repository"]["pullRequest"]
    with (
        patch.object(m1, "_github_client", return_value=client),
        patch.object(m1, "_poll_merge_state", return_value=clean),
        patch.object(m1, "_m2_gate_preflight", return_value=[]),
        patch.object(m1, "_post_merge_pytest", return_value=0),
        patch.object(m1, "_find_companion_pr") as companion,
    ):
        companion.return_value = None
        result = m1.run(_ctx(has_diary_changes="false"))
        companion.assert_not_called()
        assert result.exit_code == 0

        client.api_request.side_effect = [
            json.loads(PR_LIST_SUCCESS_JSON),
            json.loads(PR_DETAIL_OPEN_JSON),
        ]
        result2 = m1.run(_ctx(has_diary_changes="true"))
        companion.assert_called()
        assert result2.exit_code == 0
        assert result2.pipeline_status == "MERGE_REPORTED"


def test_merge_tree_invokes_git_subprocess(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    with patch.object(m1.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),  # fetch
            MagicMock(returncode=0, stdout="tree\n", stderr=""),  # merge-tree
        ]
        result, verified = m1._local_merge_verify(
            str(wt), "main", "feat/issue-3173-eb3c5291-diary"
        )
    assert result == "CLEAN"
    assert verified is True
    assert any("merge-tree" in c.args[0] for c in run.call_args_list)


# --- m1.version_behind_base (nexus #3936) ---


def _git(cwd: Path, *args: str) -> str:
    import subprocess

    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout


def _set_version(repo: Path, version: str) -> None:
    (repo / "pyproject.toml").write_text(
        f'[project]\nname = "demo"\nversion = "{version}"\n', encoding="utf-8"
    )


def _parallel_bump_repo(tmp_path: Path, branch_ver: str, base_ver: str) -> Path:
    """origin/main and origin/feat both moved 0.80.0 → their own version (parallel publish)."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    wt = tmp_path / "wt"
    _git(tmp_path, "clone", str(origin), str(seed))
    for repo in (seed,):
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "T")
    _set_version(seed, "0.80.0")
    _git(seed, "add", ".")
    _git(seed, "commit", "-m", "init")
    _git(seed, "push", "origin", "HEAD:main")
    _git(tmp_path, "clone", str(origin), str(wt))
    _git(wt, "config", "user.email", "t@t.com")
    _git(wt, "config", "user.name", "T")
    _git(wt, "checkout", "-b", "feat/issue-3173-eb3c5291-diary")
    (wt / "notes.txt").write_text("feature\n", encoding="utf-8")
    _set_version(wt, branch_ver)
    _git(wt, "add", ".")
    _git(wt, "commit", "-m", f"chore: bump version to {branch_ver} (Z: Z)")
    _git(wt, "push", "-u", "origin", "HEAD")
    _set_version(seed, base_ver)
    _git(seed, "commit", "-am", f"chore: bump version to {base_ver} (Z: Z)")
    _git(seed, "push", "origin", "HEAD:main")
    return wt


def _in_process_bump(worktree: Path, base_branch: str):
    import contextlib
    import io
    import subprocess

    from issuesmith.ops.version_bump import run_bump

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = run_bump(worktree, base_branch)
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out.getvalue(), stderr="")


def _origin_branch_version(wt: Path) -> str:
    _git(wt, "fetch", "origin")
    text = _git(wt, "show", "origin/feat/issue-3173-eb3c5291-diary:pyproject.toml")
    return text.split('version = "', 1)[1].split('"', 1)[0]


def _run_clean(wt: Path, **patches):
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_OPEN_JSON),
    ]
    clean = json.loads(GQL_CLEAN_JSON)["data"]["repository"]["pullRequest"]
    bump = patches.get("bump", _in_process_bump)
    with (
        patch.object(m1, "_github_client", return_value=client),
        patch.object(m1, "_poll_merge_state", return_value=clean) as poll,
        patch.object(m1, "_m2_gate_preflight", return_value=[]),
        patch.object(m1, "_post_merge_pytest", return_value=0),
        patch("issuesmith.gates.m1.run_version_bump", side_effect=bump),
    ):
        result = m1.run(_ctx(worktree_path=str(wt)))
    return client, poll, result


def test_version_behind_base_bumps_pushes_then_merges(tmp_path: Path, capsys) -> None:
    """AC-1: branch 0.81.0 == base 0.81.0 → 0.81.1 pushed, then the PR is merged."""
    wt = _parallel_bump_repo(tmp_path, "0.81.0", "0.81.0")
    client, poll, result = _run_clean(wt)
    assert result.pipeline_status == "MERGE_REPORTED"
    assert _origin_branch_version(wt) == "0.81.1"
    client.pr_merge.assert_called_once()
    assert poll.call_count == 2  # merge state is re-read after the push
    out = capsys.readouterr().out
    assert "VERSION_BEHIND_BASE: FIXED" in out
    assert "MERGE_FAILED_STAGES: (none)" in out


def test_version_behind_base_branch_ahead_merges_without_bump(tmp_path: Path, capsys) -> None:
    """AC-2: branch 0.82.0 > base 0.81.0 → no bump commit, merge as before."""
    wt = _parallel_bump_repo(tmp_path, "0.82.0", "0.81.0")
    head = _git(wt, "rev-parse", "HEAD")
    client, poll, result = _run_clean(wt)
    assert _git(wt, "rev-parse", "HEAD") == head
    assert _origin_branch_version(wt) == "0.82.0"
    client.pr_merge.assert_called_once()
    assert poll.call_count == 1
    assert "VERSION_BEHIND_BASE: OK" in capsys.readouterr().out


def test_version_behind_base_fix_failure_blocks_merge(tmp_path: Path, capsys) -> None:
    import subprocess

    wt = _parallel_bump_repo(tmp_path, "0.81.0", "0.81.0")
    head = _git(wt, "rev-parse", "HEAD")

    def _fail(worktree: Path, base_branch: str):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")

    client, _poll, result = _run_clean(wt, bump=_fail)
    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_REPORTED"
    client.pr_merge.assert_not_called()
    assert _git(wt, "rev-parse", "HEAD") == head
    out = capsys.readouterr().out
    assert "MERGE_FAILED_STAGES:version_behind_base" in out


def test_version_behind_base_skipped_when_not_clean(tmp_path: Path, capsys) -> None:
    """No push for a PR that will not be merged in this run."""
    wt = _parallel_bump_repo(tmp_path, "0.81.0", "0.81.0")
    head = _git(wt, "rev-parse", "HEAD")
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_OPEN_JSON),
    ]
    blocked = json.loads(GQL_BLOCKED_JSON)["data"]["repository"]["pullRequest"]
    with (
        patch.object(m1, "_github_client", return_value=client),
        patch.object(m1, "_poll_merge_state", return_value=blocked),
        patch.object(m1, "_m2_gate_preflight", return_value=[]),
    ):
        m1.run(_ctx(worktree_path=str(wt)))
    assert _git(wt, "rev-parse", "HEAD") == head
    client.pr_merge.assert_not_called()
    assert "VERSION_BEHIND_BASE: skipped" in capsys.readouterr().out
