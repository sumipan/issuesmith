"""Develop merge redo and no-diff completion (refs sumipan/nexus#8023)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from issuesmith.config import StepConfig
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.lines.develop import merge as develop_merge


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _ctx(worktree: Path, *, branch: str = "feat/x") -> StepContext:
    return StepContext(
        issue_number="99",
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        target_repo="o/r",
        issue_repo="o/r",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch=branch,
    )


def test_merge_without_commits_closes_without_push_or_pr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC8."""
    worktree = tmp_path / "wt"
    subprocess.run(["git", "init", "-q", "-b", "main", str(worktree)], check=True, capture_output=True)
    _git(worktree, "config", "user.email", "t@example.com")
    _git(worktree, "config", "user.name", "T")
    (worktree / "f").write_text("x\n", encoding="utf-8")
    _git(worktree, "add", "f")
    _git(worktree, "commit", "-m", "base")
    publish_calls: list[int] = []
    client = MagicMock()
    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 0)
    monkeypatch.setattr(develop_merge, "get_forge", lambda **_k: client)
    monkeypatch.setattr(develop_merge, "publish_branch", lambda *_a, **_k: publish_calls.append(1))
    monkeypatch.setattr(develop_merge, "cleanup_worktrees", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "cleanup_branches", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "close_issue", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "write_result", lambda *_a, **_k: True)
    monkeypatch.setattr(develop_merge, "read_result", lambda *_a, **_k: None)
    from types import SimpleNamespace

    from issuesmith.config import get_config

    lang = get_config().language
    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: False,
            host_is_repo_checkout=True,
            root=Path("."),
            language=lang,
            merge=get_config().merge,
            steps={},
        ),
    )
    result = develop_merge.run(_ctx(worktree), StepConfig())
    assert result.status == "done"
    assert publish_calls == []


def test_no_pr_rebase_conflict_redoes_step_then_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC9."""
    calls: list[int] = []

    def fake_flow(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return StepResult(
                status="andon",
                andon=Andon(kind="blocked", rule_id="merge.rebase_conflict", summary="conflict"),
            )
        return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)

    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 1)
    monkeypatch.setattr(develop_merge, "_prepare_redo", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_invoke_redo_step", lambda *_a, **_k: StepResult(status="done", markers=["PIN_DONE"]))
    monkeypatch.setattr(develop_merge, "_merge_without_pr_flow", fake_flow)
    from types import SimpleNamespace

    from issuesmith.config import StepConfig, get_config

    lang = get_config().language
    step = StepConfig(redo_on_conflict="pin")
    steps = {"pin": StepConfig(module="issuesmith.lines.develop.pin")}
    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: True,
            host_is_repo_checkout=True,
            root=Path("."),
            language=lang,
            merge=get_config().merge,
            steps=steps,
        ),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = develop_merge.run(_ctx(worktree), step)
    assert result.status == "done"
    assert len(calls) == 2


def test_no_pr_push_rejection_redoes_step_then_merges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC10."""
    calls: list[int] = []

    def fake_flow(*_a, **_k):
        calls.append(1)
        if len(calls) == 1:
            return StepResult(
                status="andon",
                andon=Andon(kind="blocked", rule_id="merge.push_rejected", summary="reject"),
            )
        return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)

    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 1)
    monkeypatch.setattr(develop_merge, "_prepare_redo", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_invoke_redo_step", lambda *_a, **_k: StepResult(status="done"))
    monkeypatch.setattr(develop_merge, "_merge_without_pr_flow", fake_flow)
    from types import SimpleNamespace

    from issuesmith.config import get_config

    step = StepConfig(redo_on_conflict="pin")
    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: True,
            host_is_repo_checkout=True,
            root=Path("."),
            language=get_config().language,
            merge=get_config().merge,
            steps={"pin": StepConfig(module="issuesmith.lines.develop.pin")},
        ),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = develop_merge.run(_ctx(worktree), step)
    assert result.status == "done"
    assert len(calls) == 2


def test_pr_conflict_redoes_step_and_force_pushes_with_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC11."""
    pushed: list[str] = []

    def fake_pr(*_a, use_lease=None, **_k):
        if use_lease is None:
            return StepResult(
                status="andon",
                andon=Andon(kind="blocked", summary="PR #1 is not mergeable"),
            )
        pushed.append(use_lease)
        return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)

    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 1)
    monkeypatch.setattr(develop_merge, "_remote_branch_sha", lambda *_a, **_k: "deadbeef")
    monkeypatch.setattr(develop_merge, "_prepare_redo", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_invoke_redo_step", lambda *_a, **_k: StepResult(status="done"))
    monkeypatch.setattr(develop_merge, "_merge_pr_path", fake_pr)
    from types import SimpleNamespace

    from issuesmith.config import get_config

    step = StepConfig(redo_on_conflict="pin")
    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: False,
            host_is_repo_checkout=True,
            root=Path("."),
            language=get_config().language,
            merge=get_config().merge,
            steps={"pin": StepConfig(module="issuesmith.lines.develop.pin")},
        ),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = develop_merge.run(_ctx(worktree), step)
    assert result.status == "done"
    assert pushed == ["deadbeef"]


def test_merge_propagates_non_done_redo_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC12."""
    andon = Andon(kind="decision", rule_id="pin.title_format", summary="bad title")
    redo_result = StepResult(status="andon", andon=andon)
    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        develop_merge,
        "_merge_without_pr_flow",
        lambda *_a, **_k: StepResult(
            status="andon",
            andon=Andon(kind="blocked", rule_id="merge.rebase_conflict", summary="x"),
        ),
    )
    monkeypatch.setattr(develop_merge, "_prepare_redo", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_invoke_redo_step", lambda *_a, **_k: redo_result)
    from types import SimpleNamespace

    from issuesmith.config import get_config

    step = StepConfig(redo_on_conflict="pin")
    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: True,
            host_is_repo_checkout=True,
            root=Path("."),
            language=get_config().language,
            merge=get_config().merge,
            steps={"pin": StepConfig(module="issuesmith.lines.develop.pin")},
        ),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = develop_merge.run(_ctx(worktree), step)
    assert result is redo_result


def test_merge_stops_after_two_redos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refs sumipan/nexus#8023 AC13."""
    blocked = StepResult(
        status="andon",
        andon=Andon(kind="blocked", rule_id="merge.rebase_conflict", summary="conflict"),
    )
    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 1)
    monkeypatch.setattr(develop_merge, "_merge_without_pr_flow", lambda *_a, **_k: blocked)
    monkeypatch.setattr(develop_merge, "_prepare_redo", lambda *_a, **_k: None)
    monkeypatch.setattr(develop_merge, "_invoke_redo_step", lambda *_a, **_k: StepResult(status="done"))
    from types import SimpleNamespace

    from issuesmith.config import get_config

    step = StepConfig(redo_on_conflict="pin")
    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: True,
            host_is_repo_checkout=True,
            root=Path("."),
            language=get_config().language,
            merge=get_config().merge,
            steps={"pin": StepConfig(module="issuesmith.lines.develop.pin")},
        ),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = develop_merge.run(_ctx(worktree), step)
    assert result.status == "andon"
    assert result.andon is not None
    assert "redo attempts=2" in result.andon.summary


def test_merge_without_redo_keeps_existing_andon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC14."""
    blocked = StepResult(
        status="andon",
        andon=Andon(kind="blocked", rule_id="merge.rebase_conflict", summary="conflict only"),
    )
    monkeypatch.setattr(develop_merge, "_commit_gate_autofixes", lambda _w: "")
    monkeypatch.setattr(develop_merge, "_bump_before_publish", lambda *_a, **_k: "")
    monkeypatch.setattr(develop_merge, "_commits_ahead", lambda *_a, **_k: 1)
    monkeypatch.setattr(develop_merge, "_merge_without_pr_flow", lambda *_a, **_k: blocked)
    from types import SimpleNamespace

    from issuesmith.config import get_config

    monkeypatch.setattr(
        develop_merge,
        "get_config",
        lambda: SimpleNamespace(
            merges_without_pr=lambda _r: True,
            host_is_repo_checkout=True,
            root=Path("."),
            language=get_config().language,
            merge=get_config().merge,
            steps={},
        ),
    )
    worktree = tmp_path / "wt"
    worktree.mkdir()
    result = develop_merge.run(_ctx(worktree), StepConfig())
    assert result is blocked
