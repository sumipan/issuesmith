"""BaseFreshnessGate.fix falls back to rebase and reports failure; _run_pytest has a timeout."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.gates import worktree as wt


def _proc(rc: int, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=rc, stdout="", stderr=stderr)


def _calls(mock) -> list[list[str]]:
    return [list(c.args[0]) for c in mock.call_args_list]


def test_fix_fast_forward_success_does_not_rebase(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    with patch.object(wt.subprocess, "run", side_effect=[_proc(0)]) as run:
        gate.fix(MagicMock())
    assert _calls(run) == [["git", "merge", "--ff-only", "origin/main"]]


def test_fix_diverged_branch_rebases_onto_base(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    with patch.object(wt.subprocess, "run", side_effect=[_proc(128, "fatal: Not possible to fast-forward"), _proc(0)]) as run:
        gate.fix(MagicMock())
    assert _calls(run) == [
        ["git", "merge", "--ff-only", "origin/main"],
        ["git", "rebase", "origin/main"],
    ]


def test_fix_rebase_conflict_aborts_and_raises(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    seq = [_proc(128, "fatal: Not possible to fast-forward"), _proc(1, "CONFLICT (content): CHANGELOG.md"), _proc(0)]
    with patch.object(wt.subprocess, "run", side_effect=seq) as run:
        with pytest.raises(RuntimeError, match="base_freshness auto-fix failed"):
            gate.fix(MagicMock())
    assert _calls(run)[-1] == ["git", "rebase", "--abort"]


def test_run_pytest_timeout_returns_124(tmp_path: Path):
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=kwargs.get("timeout"), output=b"collected 10 items\n")

    with patch.object(wt.subprocess, "run", side_effect=_timeout):
        rc, out = wt._run_pytest(tmp_path, ["tests"], timeout=1.0)
    assert rc == 124
    assert out.startswith("pytest timed out after 1 s")
    assert "collected 10 items" in out


def test_pytest_timeout_env_override(monkeypatch):
    monkeypatch.setenv(wt._PYTEST_TIMEOUT_ENV, "42")
    assert wt._pytest_timeout_sec() == 42.0
    monkeypatch.setenv(wt._PYTEST_TIMEOUT_ENV, "not-a-number")
    assert wt._pytest_timeout_sec() == wt._PYTEST_TIMEOUT_DEFAULT_SEC
