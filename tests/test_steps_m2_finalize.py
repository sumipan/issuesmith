"""tests for issuesmith.steps.m2_finalize Python step (#2869)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from issuesmith.steps.base import StepContext
from issuesmith.steps.m2_finalize import GateMaterializationError, run


def _ctx(**overrides: str) -> StepContext:
    base = {
        "issue_number": "42",
        "base_branch": "main",
        "handler_name": "merge",
        "is_cross_repo": "false",
        "target_clone_path": "",
        "source": "",
        "workflow_name": "issuesmith",
        "m1_result_filename": "m1.md",
        "m1r_result_filename": "m1r.md",
    }
    base.update(overrides)
    return StepContext(**base)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "## 受け入れ条件\n\n- [x] done\n",
        "labels": [{"name": "issuesmith:merge-running"}],
        "state": "OPEN",
    }
    return client


@pytest.fixture
def gate_patches(mock_client, tmp_path):
    gate_result = {"action": "proceed", "unchecked_count": 0, "contract_failures": []}
    with (
        patch("issuesmith.steps.m2_finalize._github_client", return_value=mock_client),
        patch("issuesmith.steps.m2_finalize._run_label_hygiene", return_value=0),
        patch("issuesmith.steps.m2_finalize._run_gate", return_value=gate_result),
        patch("issuesmith.steps.m2_finalize._run_guarded_compaction", return_value=0) as guard,
        patch("issuesmith.steps.m2_finalize._cleanup_worktrees"),
        patch("issuesmith.steps.m2_finalize._transition") as transition,
        patch("issuesmith.steps.m2_finalize._close_issue_if_open") as close_issue,
    ):
        yield {
            "client": mock_client,
            "gate": gate_result,
            "guard": guard,
            "transition": transition,
            "close_issue": close_issue,
        }


def test_proceed_transitions_to_merge_done(gate_patches):
    result = run(_ctx())

    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_DONE"
    gate_patches["transition"].assert_called_once_with(42, "issuesmith:merge-done")
    gate_patches["close_issue"].assert_called_once_with(gate_patches["client"], 42)
    gate_patches["guard"].assert_not_called()


def test_proceed_from_develop_running_hops_via_develop_done(gate_patches):
    gate_patches["client"].issue_get.return_value = {
        "body": "",
        "labels": [{"name": "issuesmith:develop-running"}],
        "state": "OPEN",
    }

    result = run(_ctx())

    assert result.exit_code == 0
    assert [c.args for c in gate_patches["transition"].call_args_list] == [
        (42, "issuesmith:develop-done"),
        (42, "issuesmith:merge-done"),
    ]


def test_migrate_transitions_to_migrate_ready(mock_client):
    gate_result = {"action": "migrate", "unchecked_count": 2, "contract_failures": []}
    with (
        patch("issuesmith.steps.m2_finalize._github_client", return_value=mock_client),
        patch("issuesmith.steps.m2_finalize._run_label_hygiene", return_value=0),
        patch("issuesmith.steps.m2_finalize._run_gate", return_value=gate_result),
        patch("issuesmith.steps.m2_finalize._transition") as transition,
        patch("issuesmith.steps.m2_finalize._run_guarded_compaction") as guard,
    ):
        result = run(_ctx(source="docs/x.md"))

    assert result.exit_code == 0
    assert result.pipeline_status == "MIGRATION_REQUIRED"
    mock_client.issue_comment.assert_called()
    assert "migrate-ready" in mock_client.issue_comment.call_args.args[1]
    transition.assert_called_once_with(42, "issuesmith:migrate-ready")
    guard.assert_not_called()


def test_retry_posts_recovery_and_blocks(mock_client):
    gate_result = {
        "action": "retry",
        "unchecked_count": 0,
        "contract_failures": ["paths_must_exist: skills/x/SKILL.md — file not found"],
    }
    mock_client.issue_get.return_value = {
        "body": "",
        "labels": [{"name": "issuesmith:develop-running"}],
        "state": "OPEN",
    }
    with (
        patch("issuesmith.steps.m2_finalize._github_client", return_value=mock_client),
        patch("issuesmith.steps.m2_finalize._run_label_hygiene", return_value=0),
        patch("issuesmith.steps.m2_finalize._run_gate", return_value=gate_result),
        patch("issuesmith.steps.m2_finalize._transition") as transition,
    ):
        result = run(_ctx())

    assert result.exit_code == 1
    assert result.pipeline_status == "MERGE_FAILED"
    assert result.recovery is not None
    assert "YAML 契約の検証に失敗" in result.recovery
    transition.assert_called_once_with(42, "issuesmith:develop-done")


def test_retry_merge_context_removes_merge_running(mock_client):
    gate_result = {"action": "retry", "unchecked_count": 1, "contract_failures": []}
    mock_client.issue_get.return_value = {
        "body": "",
        "labels": [{"name": "issuesmith:merge-running"}],
        "state": "OPEN",
    }
    with (
        patch("issuesmith.steps.m2_finalize._github_client", return_value=mock_client),
        patch("issuesmith.steps.m2_finalize._run_label_hygiene", return_value=0),
        patch("issuesmith.steps.m2_finalize._run_gate", return_value=gate_result),
        patch("issuesmith.steps.m2_finalize._transition") as transition,
    ):
        result = run(_ctx())

    assert result.exit_code == 1
    mock_client.issue_update.assert_called_with(42, labels_remove=["issuesmith:merge-running"])
    transition.assert_not_called()


def test_proceed_with_source_runs_compaction(gate_patches):
    result = run(_ctx(source="docs/x.md"))

    assert result.exit_code == 0
    gate_patches["guard"].assert_called_once()


def test_compaction_failure_does_not_block_close(mock_client):
    gate_result = {"action": "proceed", "unchecked_count": 0, "contract_failures": []}
    mock_client.issue_get.return_value = {
        "body": "",
        "labels": [{"name": "issuesmith:develop-done"}],
        "state": "OPEN",
    }
    with (
        patch("issuesmith.steps.m2_finalize._github_client", return_value=mock_client),
        patch("issuesmith.steps.m2_finalize._run_label_hygiene", return_value=0),
        patch("issuesmith.steps.m2_finalize._run_gate", return_value=gate_result),
        patch("issuesmith.steps.m2_finalize._run_guarded_compaction", return_value=1),
        patch("issuesmith.steps.m2_finalize._cleanup_worktrees"),
        patch("issuesmith.steps.m2_finalize._transition"),
        patch("issuesmith.steps.m2_finalize._close_issue_if_open") as close_issue,
    ):
        result = run(_ctx(source="docs/x.md"))

    assert result.exit_code == 0
    assert result.pipeline_status == "MERGE_DONE"
    mock_client.issue_comment.assert_called()
    assert "コンパクション失敗" in mock_client.issue_comment.call_args.args[1]
    close_issue.assert_called_once()


def test_dispatch_falls_back_to_bash_when_python_step_missing(tmp_path, monkeypatch):
    from issuesmith.ops import dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "_STEP_MODULES", {})
    monkeypatch.setattr(
        dispatch_mod,
        "_run_bash_step",
        lambda step_id, context: 0,
    )
    assert dispatch_mod.main(["m2-role-dispatch", "issue_number=1", "base_branch=main"]) == 0


def test_dispatch_uses_python_step_for_m2(monkeypatch):
    from issuesmith.ops import dispatch as dispatch_mod

    called = {}

    class FakeResult:
        exit_code = 0
        pipeline_status = "MERGE_DONE"
        recovery = None

    def fake_run(ctx):
        called["issue_number"] = ctx.issue_number
        return FakeResult()

    fake_mod = type("mod", (), {"run": staticmethod(fake_run)})
    monkeypatch.setattr("importlib.import_module", lambda name: fake_mod)

    rc = dispatch_mod.main(
        [
            "m2-role-dispatch",
            "issue_number=42",
            "base_branch=main",
            "handler_name=merge",
            "is_cross_repo=false",
            "target_clone_path=",
            "source=",
            "workflow_name=issuesmith",
            "m1_result_filename=m1.md",
            "m1r_result_filename=m1r.md",
        ]
    )
    assert rc == 0
    assert called["issue_number"] == "42"


def test_gate_materialization_failure(mock_client):
    with (
        patch("issuesmith.steps.m2_finalize._github_client", return_value=mock_client),
        patch("issuesmith.steps.m2_finalize._run_label_hygiene", return_value=0),
        patch(
            "issuesmith.steps.m2_finalize._run_gate",
            side_effect=GateMaterializationError("could not materialize origin/main"),
        ),
        patch("issuesmith.steps.m2_finalize._transition") as transition,
    ):
        result = run(_ctx())

    assert result.exit_code == 1
    assert result.pipeline_status == "MERGE_FAILED"
    transition.assert_not_called()
