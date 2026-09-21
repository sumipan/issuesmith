"""CP2 must not list *_SKIPPED as a success marker and must let RetrySignal escape."""

from __future__ import annotations

import pytest

from issuesmith.engine import RetryReason, RetrySignal
from issuesmith.steps import cp2_checkpoint as cp2
from issuesmith.steps.base import StepContext


def _ctx() -> StepContext:
    return StepContext(
        issue_number="3506",
        base_branch="main",
        handler_name="impl",
        is_cross_repo="true",
        target_clone_path=".claude/external/issuesmith",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path="wt",
        target_worktree_path="wt",
        branch="feat/issue-3506",
        target_repo="sumipan/issuesmith",
        allow_paths="- src/**",
        p1_result_filename="p1.md",
        p2_result_filename="p2.md",
        p3_result_filename="p3.md",
    )


def test_run_guarded_design_passes_no_skipped_success(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run_guarded(role, template, variables, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(cp2, "run_guarded", fake_run_guarded)
    monkeypatch.setattr(cp2, "_execution_constraints", lambda: "")
    monkeypatch.setattr(
        cp2, "resolve", lambda role, tier=None: type("Sel", (), {"model": "m", "engine": "claude"})()
    )
    rc = cp2._run_guarded_design(_ctx(), "heavy", "_cp2-checkpoint-order.md")
    assert rc == 0
    assert captured["success_statuses"] == ["CP2_PASS"]
    assert not any(s.endswith("_SKIPPED") for s in captured["success_statuses"])
    assert captured["failure_status"] == "CP2_FAILED"


class _Client:
    def issue_get(self, number, fields=None):
        return {"body": "", "labels": []}

    def issue_comment(self, number, body):
        raise AssertionError("no comment expected on RetrySignal")


def _stub_prefix(monkeypatch):
    monkeypatch.setattr(cp2, "_github_client", lambda: _Client())
    monkeypatch.setattr(cp2, "_load_pr_for_branch", lambda client, repo, branch: (10, None))
    monkeypatch.setattr(cp2, "_check_pr_scope", lambda *a, **k: None)
    monkeypatch.setattr(cp2, "_unchecked_ac_count", lambda body: 0)
    monkeypatch.setattr(cp2, "_p2_all_pass", lambda root, name: True)
    monkeypatch.setattr(cp2, "_tier_via_cli", lambda *a: "heavy")
    monkeypatch.setattr(
        cp2, "resolve", lambda role, tier=None: type("Sel", (), {"model": "m", "engine": "claude"})()
    )


def test_run_reraises_retry_signal(monkeypatch):
    _stub_prefix(monkeypatch)

    def raise_retry(ctx, tier, template):
        raise RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")

    monkeypatch.setattr(cp2, "_run_guarded_design", raise_retry)
    with pytest.raises(RetrySignal):
        cp2.run(_ctx(), None)


def test_run_reports_reason_on_other_exception(monkeypatch, capsys):
    _stub_prefix(monkeypatch)

    def raise_value(ctx, tier, template):
        raise ValueError("boom")

    monkeypatch.setattr(cp2, "_run_guarded_design", raise_value)
    result = cp2.run(_ctx(), None)
    assert result.pipeline_status == "CP2_FAILED"
    out = capsys.readouterr()
    assert "REASON: ValueError: boom" in out.out
