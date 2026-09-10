"""Fixture tests for issuesmith.steps.cp2_checkpoint (#3162).

PR JSON fixtures were captured 2026-09-11 from live GitHub REST via
``GitHubClient.api_request`` (CLAUDE.md §10):

  api_request("repos/sumipan/nexus/pulls?head=sumipan%3Afeat/issue-3172-32b12432-diary&state=open")
  → success list (additions/deletions null on list endpoint)

  api_request("repos/sumipan/nexus/pulls/3180")
  → detail with additions=3, deletions=2

  api_request("repos/sumipan/nexus/pulls?head=nobody%3Afeat/nonexistent-zzzz&state=open")
  → []
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.engine import _extract_status_values
from issuesmith.ops.dispatch import _context_to_step
from issuesmith.steps.base import StepContext
from issuesmith.steps import cp2_checkpoint as cp2

# --- Real API strings (trimmed to fields the step reads; values unchanged) ---

PR_LIST_SUCCESS_JSON = json.dumps(
    [
        {
            "number": 3180,
            "state": "open",
            "title": "実装: Issue #3172",
            "additions": None,
            "deletions": None,
            "head": {
                "label": "sumipan:feat/issue-3172-32b12432-diary",
                "ref": "feat/issue-3172-32b12432-diary",
            },
        }
    ],
    ensure_ascii=False,
)

PR_LIST_ABSENT_JSON = "[]"

PR_DETAIL_SUCCESS_JSON = json.dumps(
    {
        "number": 3180,
        "additions": 3,
        "deletions": 2,
        "state": "open",
        "head": {
            "label": "sumipan:feat/issue-3172-32b12432-diary",
            "ref": "feat/issue-3172-32b12432-diary",
        },
    },
    ensure_ascii=False,
)

# Engine-specific stdout samples: marker decoration differs; extraction must not.
_ENGINE_STDOUT = {
    "claude": "Review complete.\nPIPELINE_STATUS: CP2_PASS\n",
    "cursor": "Review complete.\n`PIPELINE_STATUS: CP2_PASS`\n",
    "codex": "Review complete.\n**PIPELINE_STATUS: CP2_PASS**\n",
}


def _ctx(**overrides: str) -> StepContext:
    base = {
        "issue_number": "3162",
        "base_branch": "main",
        "handler_name": "develop",
        "is_cross_repo": "true",
        "target_clone_path": ".claude/external/issuesmith",
        "source": "",
        "workflow_name": "issuesmith",
        "m1_result_filename": "",
        "m1r_result_filename": "",
        "worktree_path": "/tmp/wt",
        "target_worktree_path": "/tmp/twt",
        "branch": "feat/issue-3172-32b12432-diary",
        "target_repo": "sumipan/nexus",
        "allow_paths": "- src/**",
        "p2_result_filename": "p2-result.md",
    }
    base.update(overrides)
    return StepContext(**base)


def test_stepcontext_has_thirteen_new_fields_with_empty_defaults() -> None:
    ctx = StepContext(
        issue_number="1",
        base_branch="main",
        handler_name="h",
        is_cross_repo="false",
        target_clone_path="",
        source="",
        workflow_name="w",
        m1_result_filename="",
        m1r_result_filename="",
    )
    new_fields = [
        "worktree_path",
        "target_worktree_path",
        "branch",
        "target_repo",
        "allow_paths",
        "diary_worktree_path",
        "has_diary_changes",
        "pipeline_id",
        "diary_allow_paths",
        "issue_repo",
        "p1_result_filename",
        "p2_result_filename",
        "p3_result_filename",
    ]
    assert len(new_fields) == 13
    for name in new_fields:
        assert getattr(ctx, name) == ""


def test_context_to_step_maps_new_fields() -> None:
    ctx = _context_to_step(
        {
            "issue_number": "9",
            "worktree_path": "/w",
            "target_worktree_path": "/t",
            "branch": "feat/x",
            "target_repo": "sumipan/issuesmith",
            "allow_paths": "- a/**",
            "diary_worktree_path": "/d",
            "has_diary_changes": "true",
            "pipeline_id": "issue-9-abcd1234",
            "diary_allow_paths": "- workflows/**",
            "issue_repo": "sumipan/nexus",
            "p1_result_filename": "p1.md",
            "p2_result_filename": "p2.md",
            "p3_result_filename": "p3.md",
        }
    )
    assert ctx.worktree_path == "/w"
    assert ctx.target_worktree_path == "/t"
    assert ctx.branch == "feat/x"
    assert ctx.target_repo == "sumipan/issuesmith"
    assert ctx.allow_paths == "- a/**"
    assert ctx.diary_worktree_path == "/d"
    assert ctx.has_diary_changes == "true"
    assert ctx.pipeline_id == "issue-9-abcd1234"
    assert ctx.diary_allow_paths == "- workflows/**"
    assert ctx.issue_repo == "sumipan/nexus"
    assert ctx.p1_result_filename == "p1.md"
    assert ctx.p2_result_filename == "p2.md"
    assert ctx.p3_result_filename == "p3.md"


def test_pr_diff_lines_success_uses_real_list_and_detail_strings() -> None:
    client = MagicMock()
    list_payload = json.loads(PR_LIST_SUCCESS_JSON)
    detail_payload = json.loads(PR_DETAIL_SUCCESS_JSON)
    assert list_payload[0]["additions"] is None  # real list shape
    assert detail_payload["additions"] == 3 and detail_payload["deletions"] == 2

    client.api_request.side_effect = [list_payload, detail_payload]
    lines = cp2._pr_diff_lines(
        client, "sumipan/nexus", "feat/issue-3172-32b12432-diary"
    )
    assert lines == 5
    assert "head=" in client.api_request.call_args_list[0].args[0]
    assert client.api_request.call_args_list[1].args[0].endswith("/pulls/3180")


def test_pr_diff_lines_absent_uses_real_empty_list_string() -> None:
    client = MagicMock()
    absent = json.loads(PR_LIST_ABSENT_JSON)
    assert absent == []
    client.api_request.return_value = absent
    lines = cp2._pr_diff_lines(client, "sumipan/nexus", "feat/nonexistent")
    assert lines == 9999
    client.api_request.assert_called_once()


def test_unchecked_ac_count_only_inside_section() -> None:
    body = (
        "## 概要\n- [ ] ignore\n"
        "## 受け入れ条件\n- [x] done\n- [ ] todo\n- [ ] other\n"
        "## やらないこと\n- [ ] ignore2\n"
    )
    assert cp2._unchecked_ac_count(body) == 2


def test_p2_all_pass_reads_jobs_file(tmp_path: Path) -> None:
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (jobs / "p2.md").write_text("ok\nVERIFY_FAILED_CHECKS: (none)\n", encoding="utf-8")
    assert cp2._p2_all_pass(tmp_path, "p2.md") is True
    assert cp2._p2_all_pass(tmp_path, "missing.md") is False


@pytest.mark.parametrize("engine", ["claude", "cursor", "codex"])
def test_run_guarded_marker_extraction_stable_across_engines(engine: str) -> None:
    stdout = _ENGINE_STDOUT[engine]
    assert _extract_status_values(stdout) == ["CP2_PASS"]


def test_run_success_skips_fail_handler() -> None:
    client = MagicMock()
    client.api_request.side_effect = [
        json.loads(PR_LIST_SUCCESS_JSON),
        json.loads(PR_DETAIL_SUCCESS_JSON),
    ]
    client.issue_get.return_value = {"body": "## 受け入れ条件\n- [x] ok\n"}

    with (
        patch.object(cp2, "_github_client", return_value=client),
        patch.object(cp2, "_repo_root", return_value=Path("/nonexistent")),
        patch.object(cp2, "_tier_via_cli", return_value="light"),
        patch.object(cp2, "resolve", return_value=MagicMock(engine="claude", model="m")),
        patch.object(cp2, "_run_guarded_design", return_value=0) as guarded,
        patch.object(cp2, "_handle_fail") as fail,
    ):
        result = cp2.run(_ctx())

    assert result.exit_code == 0
    guarded.assert_called_once()
    fail.assert_not_called()
    client.issue_comment.assert_not_called()


def test_run_fail_comments_and_transitions_to_develop_done() -> None:
    client = MagicMock()
    client.api_request.return_value = []
    client.issue_get.side_effect = [
        {"body": "## 受け入れ条件\n- [ ] left\n"},
        {"labels": [{"name": "issuesmith:develop-running"}]},
    ]

    with (
        patch.object(cp2, "_github_client", return_value=client),
        patch.object(cp2, "_repo_root", return_value=Path("/nonexistent")),
        patch.object(cp2, "_tier_via_cli", return_value="heavy"),
        patch.object(cp2, "resolve", return_value=MagicMock(engine="cursor", model="m")),
        patch.object(cp2, "_run_guarded_design", return_value=1),
        patch.object(cp2, "_transition") as transition,
    ):
        result = cp2.run(_ctx(branch="feat/missing"))

    assert result.exit_code == 1
    assert result.pipeline_status == "CP2_FAILED"
    client.issue_comment.assert_called_once()
    assert "CP2 FAIL" in client.issue_comment.call_args.args[1]
    transition.assert_called_once_with(3162, "issuesmith:develop-done")
