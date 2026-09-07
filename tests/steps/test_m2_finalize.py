"""Tests for _materialize_gate_root retry and stderr propagation (#2919)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from issuesmith.steps.m2_finalize import GateMaterializationError, _materialize_gate_root


def _completed(rc: int, stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = rc
    proc.stderr = stderr
    return proc


@pytest.fixture
def repo_cwd(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


@pytest.fixture
def gate_root(tmp_path: Path) -> Path:
    root = tmp_path / "gate-root"
    root.mkdir()
    return root


def test_fetch_failure_includes_stderr(repo_cwd: Path, gate_root: Path) -> None:
    fetch_proc = _completed(128, "network down")

    with (
        patch("issuesmith.steps.m2_finalize.tempfile.mkdtemp", return_value=str(gate_root)),
        patch("issuesmith.steps.m2_finalize._git", return_value=fetch_proc) as mock_git,
        patch("issuesmith.steps.m2_finalize.shutil.rmtree") as mock_rmtree,
    ):
        with pytest.raises(GateMaterializationError, match="fetch_rc=128") as exc_info:
            _materialize_gate_root(repo_cwd, "main", 2919, "")

    assert "fetch_stderr='network down'" in str(exc_info.value)
    mock_git.assert_called_once()
    mock_rmtree.assert_called_once_with(gate_root, ignore_errors=True)


def test_worktree_add_failure_includes_stderr_after_max_retries(
    repo_cwd: Path, gate_root: Path
) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, "lock file exists")

    with (
        patch("issuesmith.steps.m2_finalize.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.steps.m2_finalize._git",
            side_effect=[fetch_proc, add_fail, add_fail, add_fail],
        ),
        patch("issuesmith.steps.m2_finalize.subprocess.run") as mock_run,
        patch("issuesmith.steps.m2_finalize.shutil.rmtree") as mock_rmtree,
        patch("issuesmith.steps.m2_finalize.time.sleep"),
    ):
        with pytest.raises(GateMaterializationError, match="after 3 attempts") as exc_info:
            _materialize_gate_root(repo_cwd, "main", 2919, "")

    msg = str(exc_info.value)
    assert "add_rc=1" in msg
    assert "add_stderr='lock file exists'" in msg
    assert mock_run.call_count == 3
    assert mock_rmtree.call_count == 3
    for c in mock_rmtree.call_args_list:
        assert c == call(gate_root, ignore_errors=True)


def test_prune_called_before_each_worktree_add_attempt(
    repo_cwd: Path, gate_root: Path
) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, "busy")
    add_ok = _completed(0)

    with (
        patch("issuesmith.steps.m2_finalize.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.steps.m2_finalize._git",
            side_effect=[fetch_proc, add_fail, add_ok],
        ),
        patch("issuesmith.steps.m2_finalize.subprocess.run") as mock_run,
        patch("issuesmith.steps.m2_finalize.shutil.rmtree"),
        patch("issuesmith.steps.m2_finalize.time.sleep"),
    ):
        result = _materialize_gate_root(repo_cwd, "main", 2919, "")

    assert result == gate_root
    prune_calls = [
        c
        for c in mock_run.call_args_list
        if c.args[0] == ["git", "worktree", "prune"]
    ]
    assert len(prune_calls) == 2
    for c in prune_calls:
        assert c.kwargs["cwd"] == str(repo_cwd)


def test_worktree_add_succeeds_on_second_attempt(repo_cwd: Path, gate_root: Path) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, "transient lock")
    add_ok = _completed(0)

    with (
        patch("issuesmith.steps.m2_finalize.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.steps.m2_finalize._git",
            side_effect=[fetch_proc, add_fail, add_ok],
        ),
        patch("issuesmith.steps.m2_finalize.subprocess.run"),
        patch("issuesmith.steps.m2_finalize.shutil.rmtree") as mock_rmtree,
        patch("issuesmith.steps.m2_finalize.time.sleep") as mock_sleep,
    ):
        result = _materialize_gate_root(repo_cwd, "main", 2919, "")

    assert result == gate_root
    mock_rmtree.assert_called_once_with(gate_root, ignore_errors=True)
    mock_sleep.assert_called_once_with(5)


def test_three_consecutive_add_failures_raise(repo_cwd: Path, gate_root: Path) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, "still locked")

    with (
        patch("issuesmith.steps.m2_finalize.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.steps.m2_finalize._git",
            side_effect=[fetch_proc, add_fail, add_fail, add_fail],
        ),
        patch("issuesmith.steps.m2_finalize.subprocess.run"),
        patch("issuesmith.steps.m2_finalize.shutil.rmtree") as mock_rmtree,
        patch("issuesmith.steps.m2_finalize.time.sleep") as mock_sleep,
    ):
        with pytest.raises(GateMaterializationError):
            _materialize_gate_root(repo_cwd, "main", 2919, "")

    assert mock_rmtree.call_count == 3
    assert mock_sleep.call_args_list == [call(5), call(10)]
