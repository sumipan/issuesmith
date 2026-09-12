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
            "title": "実装: Issue #3173",
            "body": "P1/P2 result より自動生成。\n\nRefs #3173",
            "draft": False,
            "head": {
                "ref": "feat/issue-3173-eb3c5291-diary",
                "label": "sumipan:feat/issue-3173-eb3c5291-diary",
            },
        }
    ],
    ensure_ascii=False,
)

PR_LIST_ABSENT_JSON = "[]"

PR_DETAIL_OPEN_JSON = json.dumps(
    {
        "number": 3183,
        "state": "open",
        "merged": False,
        "mergeable": None,
        "mergeable_state": "unknown",
        "title": "実装: Issue #3173",
        "draft": False,
        "body": "P1/P2 result より自動生成。\n\nRefs #3173",
        "head": {
            "ref": "feat/issue-3173-eb3c5291-diary",
            "label": "sumipan:feat/issue-3173-eb3c5291-diary",
        },
    },
    ensure_ascii=False,
)

PR_DETAIL_MERGED_JSON = json.dumps(
    {
        "number": 3181,
        "state": "closed",
        "merged": True,
        "mergeable": None,
        "mergeable_state": "unknown",
        "title": "実装: Issue #3128",
        "draft": False,
        "body": "P1/P2 result より自動生成。\n\nRefs #3128",
        "head": {
            "ref": "feat/issue-3128-52c4f66e",
            "label": "sumipan:feat/issue-3128-52c4f66e",
        },
    },
    ensure_ascii=False,
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
