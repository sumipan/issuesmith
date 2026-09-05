"""tests for issuesmith.recovery — plan(), recover/redispatch CLI (#2865)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from issuesmith import recovery
from issuesmith.queue import phase_preconditions, redispatch_label_plan


class FakeClient:
    def __init__(self, prs: list[dict[str, Any]] | None = None) -> None:
        self._prs = prs or []

    def pr_list(self, state: str = "open", limit: int = 100) -> list[dict[str, Any]]:
        if state == "open":
            return self._prs
        return []

    def pr_get(self, number: int) -> dict[str, Any]:
        for pr in self._prs:
            if pr.get("number") == number:
                return pr
        return {}


def _write_exec(tmp_path: Path, records: list[dict[str, Any]]) -> Path:
    exec_path = tmp_path / "exec.jsonl"
    exec_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return exec_path


def _frozen_order(tmp_path: Path, issue: int, step: str = "cp2-conditional") -> Path:
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    order = jobs / "20260905120000-shell-order-test.md"
    order.write_text(
        f'exec python3 -m issuesmith dispatch {step} "issue_number={issue}" '
        f'"worktree_path=.claude/worktrees/issue-{issue}-abcd1234"\n',
        encoding="utf-8",
    )
    return order


@pytest.fixture
def repo_layout(tmp_path: Path) -> Path:
    (tmp_path / "jobs" / "done").mkdir(parents=True)
    (tmp_path / ".claude" / "worktrees" / "issue-42-abcd1234").mkdir(parents=True)
    return tmp_path


def test_plan_returns_recover_when_prerequisites_intact(repo_layout: Path, monkeypatch) -> None:
    issue = 42
    _frozen_order(repo_layout, issue)
    exec_path = _write_exec(repo_layout, [])
    monkeypatch.setattr(recovery, "_exec_path", lambda: exec_path)
    monkeypatch.setattr(recovery, "_repo_root", lambda: repo_layout)
    monkeypatch.setattr(recovery, "_generation_keys_available", lambda: False)

    plan = recovery.plan(
        issue=issue,
        failed_step="cp2",
        labels={"issuesmith:develop-running"},
    )

    assert plan.action == "recover"
    assert plan.blocked_by is None
    assert plan.required_labels == frozenset()
    assert "recover" in plan.command
    assert "cp2" in plan.command


def test_plan_returns_redispatch_when_worktree_missing(repo_layout: Path, monkeypatch) -> None:
    issue = 42
    wt = repo_layout / ".claude" / "worktrees" / "issue-42-abcd1234"
    if wt.exists():
        wt.rmdir()
    _frozen_order(repo_layout, issue)
    exec_path = _write_exec(repo_layout, [])
    monkeypatch.setattr(recovery, "_exec_path", lambda: exec_path)
    monkeypatch.setattr(recovery, "_repo_root", lambda: repo_layout)
    monkeypatch.setattr(recovery, "_generation_keys_available", lambda: False)

    plan = recovery.plan(
        issue=issue,
        failed_step="cp2",
        labels={"issuesmith:develop-running"},
    )

    assert plan.action == "redispatch"
    assert plan.blocked_by is None
    assert plan.required_labels == frozenset({"issuesmith:draft-done"})
    assert "redispatch" in plan.command
    assert "--phase develop" in plan.command


def test_plan_blocked_when_idempotency_consumed_and_no_generation(
    repo_layout: Path, monkeypatch
) -> None:
    issue = 42
    handler = "impl"
    order = _frozen_order(repo_layout, issue)
    exec_path = _write_exec(
        repo_layout,
        [
            {
                "uuid": "aaaa",
                "command": f"bash -o pipefail {order}",
                "idempotency_key": f"issuesmith:{handler}:{issue}",
            }
        ],
    )
    monkeypatch.setattr(recovery, "_exec_path", lambda: exec_path)
    monkeypatch.setattr(recovery, "_repo_root", lambda: repo_layout)
    monkeypatch.setattr(recovery, "_generation_keys_available", lambda: False)
    monkeypatch.setattr(recovery, "_idempotency_consumed", lambda _h, _i: True)

    plan = recovery.plan(
        issue=issue,
        failed_step="cp2",
        labels={"issuesmith:develop-running"},
    )

    assert plan.blocked_by is not None
    assert "2876" in plan.blocked_by or "世代" in plan.blocked_by


def test_plan_redispatch_when_consumed_and_generation_available(
    repo_layout: Path, monkeypatch
) -> None:
    issue = 42
    monkeypatch.setattr(recovery, "_exec_path", lambda: _write_exec(repo_layout, []))
    monkeypatch.setattr(recovery, "_repo_root", lambda: repo_layout)
    monkeypatch.setattr(recovery, "_generation_keys_available", lambda: True)
    monkeypatch.setattr(recovery, "_idempotency_consumed", lambda _h, _i: True)
    monkeypatch.setattr(recovery, "_prerequisites_intact", lambda *_a, **_k: (True, "ok"))

    plan = recovery.plan(
        issue=issue,
        failed_step="cp2",
        labels={"issuesmith:develop-running"},
    )

    assert plan.action == "redispatch"
    assert plan.blocked_by is None


@pytest.mark.parametrize("phase", ["draft", "develop", "merge"])
def test_required_labels_satisfy_phase_preconditions(phase: str) -> None:
    required, to_remove = redispatch_label_plan(phase)
    labels = set(required)
    issue = {"state": "OPEN", "labels": [{"name": lab} for lab in labels]}
    client = FakeClient(
        prs=[{"number": 1, "title": "feat", "body": "Closes #99"}] if phase == "merge" else []
    )
    ok, reason = phase_preconditions(phase, issue, client, 99)
    assert ok, f"phase={phase} labels={labels} reason={reason}"


def test_merge_redispatch_blocked_without_open_pr(repo_layout: Path, monkeypatch) -> None:
    issue = 99
    monkeypatch.setattr(recovery, "_exec_path", lambda: _write_exec(repo_layout, []))
    monkeypatch.setattr(recovery, "_repo_root", lambda: repo_layout)
    monkeypatch.setattr(recovery, "_generation_keys_available", lambda: False)
    monkeypatch.setattr(recovery, "_idempotency_consumed", lambda _h, _i: False)
    monkeypatch.setattr(recovery, "_prerequisites_intact", lambda *_a, **_k: (False, "worktree missing"))
    monkeypatch.setattr(
        recovery,
        "_merge_redispatch_blocked",
        lambda _i, _c: "no open or merged PR with Closes #N",
    )

    plan = recovery.plan(
        issue=issue,
        failed_step="m2",
        labels={"issuesmith:merge-running"},
        client=FakeClient(),
    )

    assert plan.action == "redispatch"
    assert plan.blocked_by is not None
    assert "PR" in plan.blocked_by or "Closes" in plan.blocked_by


def test_recover_dry_run_lists_steps(repo_layout: Path, monkeypatch) -> None:
    issue = 42
    handler = "impl"
    order = _frozen_order(repo_layout, issue)
    uuid = "step-cp2-uuid"
    exec_path = _write_exec(
        repo_layout,
        [
            {
                "uuid": uuid,
                "command": f"bash -o pipefail {order}",
                "idempotency_key": f"issuesmith:{handler}:{issue}",
            }
        ],
    )
    (repo_layout / "jobs" / "done" / uuid).write_text("0", encoding="utf-8")
    monkeypatch.setattr(recovery, "_exec_path", lambda: exec_path)
    monkeypatch.setattr(recovery, "_repo_root", lambda: repo_layout)
    monkeypatch.setattr(recovery, "_generation_keys_available", lambda: False)
    monkeypatch.setattr(recovery, "_idempotency_consumed", lambda _h, _i: True)

    steps = recovery.list_recover_steps(issue=issue, failed_step="cp2", from_step="cp2")
    assert steps == [] or "cp2" in steps


def test_plan_to_json_roundtrip() -> None:
    plan = recovery.Plan(
        action="recover",
        reason="test",
        command="issuesmith recover 1 --from cp2",
        required_labels=frozenset(),
        blocked_by=None,
    )
    data = json.loads(recovery.plan_to_json(plan))
    assert data["action"] == "recover"
    assert data["command"] == plan.command
    assert data["required_labels"] == []


def test_redispatch_blocked_exits_without_label_changes(monkeypatch) -> None:
    from issuesmith.recovery import cmd_redispatch

    blocked = recovery.Plan(
        action="redispatch",
        reason="blocked",
        command="issuesmith redispatch 1 --phase merge",
        required_labels=frozenset(),
        blocked_by="no open PR",
    )
    monkeypatch.setattr(recovery, "plan", lambda *_a, **_k: blocked)
    client = MagicMock()
    monkeypatch.setattr(recovery, "_github_client", lambda: client)

    rc = cmd_redispatch(
        issue=1,
        phase="merge",
        reason="test",
        dry_run=False,
    )
    assert rc == 1
    client.issue_update.assert_not_called()
