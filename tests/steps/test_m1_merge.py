"""Fixture tests for issuesmith.merge helpers (#3164 / #4275).

PR / GraphQL fixtures were captured 2026-09-11 from live GitHub via
``ForgePort.api_request`` / GraphQL (CLAUDE.md section 10):

  api_request("repos/sumipan/nexus/pulls?head=sumipan%3Afeat%2Fissue-3173-eb3c5291-diary&state=open")
  -> success list (number=3183, body contains Refs #3173)

  api_request("repos/sumipan/nexus/pulls?head=nobody%3Afeat%2Fnonexistent-zzzz&state=open")
  -> []

  api_request("repos/sumipan/nexus/pulls/3183")
  -> merged=false, mergeable_state=unknown

  api_request("repos/sumipan/nexus/pulls/3181")
  -> merged=true, state=closed

  GraphQL pullRequest(number:3183) (retry until computed)
  -> mergeStateStatus=CLEAN, mergeable=MERGEABLE

  GraphQL BLOCKED: live BLOCKED PR was not found in sumipan/{nexus,issuesmith}
  on 2026-09-11; fixture uses the same envelope/fields as the live CLEAN
  capture with mergeStateStatus=BLOCKED (valid MergeStateStatus enum).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith import merge as merge_api

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


def test_find_pr_success_uses_real_list_string() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_SUCCESS_JSON)
    result = merge_api.find_pr(
        client,
        "sumipan/nexus",
        "feat/issue-3173-eb3c5291-diary",
        3173,
    )
    assert result.number == 3183
    assert result.state == "open"
    assert result.stage == "branch"
    assert "head=" in client.api_request.call_args.args[0]


def test_find_pr_absent_uses_real_empty_list_string() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_ABSENT_JSON)
    result = merge_api.find_pr(client, "sumipan/nexus", "feat/nonexistent-zzzz", 9999)
    assert result.number is None
    assert result.state == ""
    assert result.stage == ""


def test_already_merged_true_uses_real_merged_field() -> None:
    detail = json.loads(PR_DETAIL_MERGED_JSON)
    assert detail["merged"] is True
    client = MagicMock()
    client.api_request.return_value = detail
    assert merge_api.is_already_merged(client, "sumipan/nexus", 3181) is True


def test_already_merged_false_uses_real_open_detail() -> None:
    detail = json.loads(PR_DETAIL_OPEN_JSON)
    assert detail["merged"] is False
    client = MagicMock()
    client.api_request.return_value = detail
    assert merge_api.is_already_merged(client, "sumipan/nexus", 3183) is False


def test_merge_state_clean_parses_real_graphql_string() -> None:
    payload = json.loads(GQL_CLEAN_JSON)
    pr = payload["data"]["repository"]["pullRequest"]
    assert pr["mergeStateStatus"] == "CLEAN"
    assert pr["mergeable"] == "MERGEABLE"
    client = MagicMock()
    with patch.object(merge_api, "graphql_merge_state", return_value=merge_api.MergeStateInfo.from_mapping(pr)):
        got = merge_api.get_merge_state(client, "sumipan/nexus", 3183)
    assert got.merge_state_status == "CLEAN"
    assert got.head_ref_name == "feat/issue-3173-eb3c5291-diary"


def test_merge_state_blocked_parses_graphql_blocked_fixture() -> None:
    payload = json.loads(GQL_BLOCKED_JSON)
    pr = payload["data"]["repository"]["pullRequest"]
    assert pr["mergeStateStatus"] == "BLOCKED"
    client = MagicMock()
    with patch.object(merge_api, "graphql_merge_state", return_value=merge_api.MergeStateInfo.from_mapping(pr)):
        got = merge_api.get_merge_state(client, "sumipan/nexus", 3183)
    assert got.merge_state_status == "BLOCKED"


def test_graphql_merge_state_uses_client_graphql_data() -> None:
    payload = json.loads(GQL_CLEAN_JSON)
    data = payload["data"]
    client = MagicMock()
    client.graphql.return_value = data
    got = merge_api.graphql_merge_state(client, "sumipan/nexus", 3183)
    assert got.merge_state_status == "CLEAN"
    assert got.head_ref_name == "feat/issue-3173-eb3c5291-diary"
    client.graphql.assert_called_once()
    _, variables = client.graphql.call_args.args
    assert variables == {"owner": "sumipan", "name": "nexus", "number": 3183}
    client.pr_get.assert_not_called()


def test_graphql_merge_state_fallback_not_implemented(capsys) -> None:
    detail = json.loads(PR_DETAIL_OPEN_JSON)
    client = MagicMock()
    client.graphql.side_effect = NotImplementedError("no graphql")
    client.pr_get.return_value = detail
    got = merge_api.graphql_merge_state(client, "sumipan/nexus", 3183)
    client.pr_get.assert_called_once_with(3183, repo="sumipan/nexus")
    assert got.merge_state_status == "UNKNOWN"
    assert "falling back to pr_get" in capsys.readouterr().err


def test_graphql_merge_state_fallback_on_exception(capsys) -> None:
    detail = json.loads(PR_DETAIL_OPEN_JSON)
    client = MagicMock()
    client.graphql.side_effect = RuntimeError("network")
    client.pr_get.return_value = detail
    merge_api.graphql_merge_state(client, "sumipan/nexus", 3183)
    client.pr_get.assert_called_once_with(3183, repo="sumipan/nexus")
    assert "falling back to pr_get" in capsys.readouterr().err


def test_graphql_merge_state_fallback_invalid_repo() -> None:
    detail = json.loads(PR_DETAIL_OPEN_JSON)
    client = MagicMock()
    client.pr_get.return_value = detail
    merge_api.graphql_merge_state(client, "invalid-repo", 3183)
    client.graphql.assert_not_called()
    client.pr_get.assert_called_once_with(3183, repo="invalid-repo")


def test_graphql_merge_state_fallback_when_merge_state_missing() -> None:
    detail = json.loads(PR_DETAIL_OPEN_JSON)
    client = MagicMock()
    client.graphql.return_value = {"repository": {"pullRequest": {}}}
    client.pr_get.return_value = detail
    merge_api.graphql_merge_state(client, "sumipan/nexus", 3183)
    client.pr_get.assert_called_once_with(3183, repo="sumipan/nexus")


def test_wait_merge_state_retries_on_blocked() -> None:
    blocked = merge_api.MergeStateInfo.from_mapping(
        json.loads(GQL_BLOCKED_JSON)["data"]["repository"]["pullRequest"]
    )
    clean = merge_api.MergeStateInfo.from_mapping(
        json.loads(GQL_CLEAN_JSON)["data"]["repository"]["pullRequest"]
    )
    client = MagicMock()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with patch.object(
        merge_api,
        "get_merge_state",
        side_effect=[blocked, blocked, clean],
    ):
        got = merge_api.wait_merge_state(
            client,
            "sumipan/nexus",
            3183,
            timeout_seconds=120.0,
            poll_interval_seconds=1.0,
            sleep=fake_sleep,
        )
    assert got.merge_state_status == "CLEAN"
    assert sleeps == [5.0, 10.0]


def test_wait_merge_state_timeout_includes_last_state_and_pr_number() -> None:
    blocked = merge_api.MergeStateInfo.from_mapping(
        json.loads(GQL_BLOCKED_JSON)["data"]["repository"]["pullRequest"]
    )
    client = MagicMock()

    with (
        patch.object(merge_api, "get_merge_state", return_value=blocked),
        patch.object(merge_api, "time") as mock_time,
    ):
        mock_time.monotonic.side_effect = [0.0, 0.0, 10.0]
        with pytest.raises(merge_api.MergeStateTimeoutError) as exc_info:
            merge_api.wait_merge_state(
                client,
                "sumipan/nexus",
                3183,
                timeout_seconds=5.0,
                poll_interval_seconds=1.0,
                blocked_backoff_seconds=(),
                sleep=lambda _s: None,
            )
    err = exc_info.value
    assert err.pr_number == 3183
    assert err.last_state.merge_state_status == "BLOCKED"


def test_find_companion_pr_uses_branch_suffix() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_SUCCESS_JSON)
    number = merge_api.find_companion_pr(
        client,
        "sumipan/nexus",
        "feat/issue-3173-eb3c5291",
        companion_suffix="-diary",
    )
    assert number == 3183
    assert "head=" in client.api_request.call_args.args[0]


def test_check_companion_ready_requires_approval_and_ci() -> None:
    client = MagicMock()
    client.api_request.return_value = [{"state": "APPROVED"}]
    client.pr_checks.return_value = [{"conclusion": "success"}]
    result = merge_api.check_companion_ready(client, "sumipan/nexus", 3183)
    assert result.ready is True
    assert result.review_decision == "APPROVED"
    assert result.ci_ok is True


def test_merge_tree_invokes_git_subprocess(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    with patch.object(merge_api.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="tree\n", stderr=""),
        ]
        result = merge_api.verify_local_merge(
            str(wt), "main", "feat/issue-3173-eb3c5291-diary"
        )
    assert result.result == "CLEAN"
    assert result.verified is True
    assert result.cwd == str(wt)
    assert result.fetch_command is not None
    assert result.merge_tree_command is not None
    assert any("merge-tree" in c.args[0] for c in run.call_args_list)


def test_run_post_merge_pytest_returns_command_cwd_and_exit_code(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "src").mkdir()
    with patch.object(merge_api.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="ok\n", stderr=""),
        ]
        result = merge_api.run_post_merge_pytest(str(wt))
    assert result.cwd == str(wt)
    assert result.command == ["python3", "-m", "pytest", "-q"]
    assert result.exit_code == 0
    assert result.pull_command == ["git", "pull", "origin", "HEAD"]


def test_run_post_merge_pytest_propagates_failure_exit_code(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "src").mkdir()
    with patch.object(merge_api.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="fail\n"),
        ]
        result = merge_api.run_post_merge_pytest(str(wt))
    assert result.exit_code == 1
