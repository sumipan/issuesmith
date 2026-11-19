"""Refs sumipan/issuesmith#7402 — WIP preservation on interrupted implementation."""

from __future__ import annotations

import os
import signal
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.metrics import FailureClass
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.contract import RetryReason, RetrySignal
from issuesmith.engine import RoleSelection, _execute, count_commits_excluding_wip


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture()
def impl_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("init\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _exe_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    snap = QuotaSnapshot(
        engines={
            "claude": EngineQuotaState(
                status="available",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                resume_at=None,
                reason=None,
            ),
            "codex": EngineQuotaState(
                status="available",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                resume_at=None,
                reason=None,
            ),
        },
        draining_engines={},
        running_tasks={},
        limits={},
    )
    qg.snapshot.return_value = snap


def _llm_result(*, rc: int = 1, failure_class=None, body: str = ""):
    return LLMResult(
        stdout="",
        stderr="",
        body=body,
        usage=None,
        returncode=rc,
        failure_class=failure_class,
        engine_used="claude",
        model_used="m",
        attempts=1,
        quota_reported=False,
        additional_tags={},
    )


def test_wip_commit_on_timeout_excludes_runtime_state_dirs(
    impl_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC1 — timeout preserves source, not jobs/logs/.pipeline-state."""
    _exe_mocks(monkeypatch)

    def _fake_call(*_a, **kwargs):
        cwd = kwargs.get("cwd")
        assert cwd is not None
        root = Path(cwd)
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "a.py").write_text("x\n", encoding="utf-8")
        (root / "jobs").mkdir(exist_ok=True)
        (root / "jobs" / "x.jsonl").write_text("{}\n", encoding="utf-8")
        (root / "logs").mkdir(exist_ok=True)
        (root / "logs" / "y.json").write_text("{}\n", encoding="utf-8")
        (root / ".pipeline-state").mkdir(exist_ok=True)
        (root / ".pipeline-state" / "z.yml").write_text("k: v\n", encoding="utf-8")
        return _llm_result(rc=124, failure_class=FailureClass.TIMEOUT)

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_fake_call))

    proc = _execute("implementation", "go", cwd=str(impl_repo))

    assert proc.returncode == 124
    log = _git(impl_repo, "log", "-1", "--format=%B").stdout
    assert log.splitlines()[0] == "wip: preserved from interrupted implementation (timeout)"
    assert "Issuesmith-Wip: true" in log
    tree = _git(impl_repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "src/a.py" in tree
    assert "jobs/x.jsonl" not in tree
    assert "logs/y.json" not in tree
    assert ".pipeline-state/z.yml" not in tree


def test_wip_commit_on_sigterm_restores_handler(
    impl_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC2 — SIGTERM commits work and restores the prior handler."""
    _exe_mocks(monkeypatch)
    prior = signal.getsignal(signal.SIGTERM)

    def _fake_call(*_a, **kwargs):
        cwd = kwargs.get("cwd")
        (Path(cwd) / "work.txt").write_text("w\n", encoding="utf-8")
        os.kill(os.getpid(), signal.SIGTERM)
        return _llm_result(rc=0)

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_fake_call))

    with pytest.raises(SystemExit) as exc:
        _execute("implementation", "go", cwd=str(impl_repo))
    assert exc.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) is prior
    log = _git(impl_repo, "log", "-1", "--format=%B").stdout
    assert "signal SIGTERM" in log.splitlines()[0]


def test_wip_commit_on_quota_exhausted_retry(
    impl_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC3 — QUOTA_EXHAUSTED raises RetrySignal and leaves a WIP commit."""
    _exe_mocks(monkeypatch)

    def _fake_call(*_a, **kwargs):
        (Path(kwargs["cwd"]) / "a.txt").write_text("a\n", encoding="utf-8")
        return _llm_result(rc=1, failure_class=FailureClass.QUOTA_EXHAUSTED)

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_fake_call))

    with pytest.raises(RetrySignal) as exc:
        _execute("implementation", "go", cwd=str(impl_repo))
    assert exc.value.reason is RetryReason.QUOTA_PAUSED
    log = _git(impl_repo, "log", "-1", "--format=%B").stdout
    assert "retry quota_paused" in log.splitlines()[0]


def test_successful_implementation_leaves_changes_uncommitted(
    impl_repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC4 — exit 0 does not create a WIP commit."""
    _exe_mocks(monkeypatch)

    def _fake_call(*_a, **kwargs):
        (Path(kwargs["cwd"]) / "keep.py").write_text("k\n", encoding="utf-8")
        return _llm_result(rc=0)

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_fake_call))

    proc = _execute("implementation", "go", cwd=str(impl_repo))
    assert proc.returncode == 0
    assert _git(impl_repo, "status", "--porcelain").stdout.strip()
    assert _git(impl_repo, "rev-list", "--count", "HEAD").stdout.strip() == "1"


def test_count_commits_excluding_wip_skips_trailer_commits(impl_repo: Path) -> None:
    """Refs #7402 AC5 — wip commits are not counted toward progress."""
    _git(impl_repo, "checkout", "-b", "work")
    (impl_repo / "n1.txt").write_text("1\n", encoding="utf-8")
    _git(impl_repo, "add", "n1.txt")
    _git(impl_repo, "commit", "-m", "normal")
    (impl_repo / "n2.txt").write_text("2\n", encoding="utf-8")
    _git(impl_repo, "add", "n2.txt")
    _git(
        impl_repo,
        "-c",
        "user.name=issuesmith",
        "-c",
        "user.email=issuesmith@localhost",
        "commit",
        "-m",
        "wip\n\nIssuesmith-Wip: true\n",
    )
    assert count_commits_excluding_wip(impl_repo, "main..HEAD") == 1
