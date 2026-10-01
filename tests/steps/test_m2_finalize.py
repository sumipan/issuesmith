"""Tests for dual-root gate materialize/cleanup helpers (#2919 / #4275)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from issuesmith.ac_contract import (
    GateMaterializationError,
    cleanup_gate_root,
    dual_gate_roots,
    materialize_gate_root,
)


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
        patch("issuesmith.ac_contract.tempfile.mkdtemp", return_value=str(gate_root)),
        patch("issuesmith.ac_contract._git", return_value=fetch_proc) as mock_git,
        patch("issuesmith.ac_contract.shutil.rmtree") as mock_rmtree,
    ):
        with pytest.raises(GateMaterializationError, match="fetch_rc=128") as exc_info:
            materialize_gate_root(repo_cwd, "main", "gate-")

    assert "fetch_stderr='network down'" in str(exc_info.value)
    mock_git.assert_called_once()
    mock_rmtree.assert_called_once_with(gate_root, ignore_errors=True)


def test_worktree_add_failure_includes_stderr_after_max_retries(
    repo_cwd: Path, gate_root: Path
) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, "lock file exists")

    with (
        patch("issuesmith.ac_contract.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.ac_contract._git",
            side_effect=[fetch_proc, add_fail, add_fail, add_fail],
        ),
        patch("issuesmith.ac_contract.subprocess.run") as mock_run,
        patch("issuesmith.ac_contract.shutil.rmtree") as mock_rmtree,
        patch("issuesmith.ac_contract.time.sleep"),
    ):
        with pytest.raises(GateMaterializationError, match="after 3 attempts") as exc_info:
            materialize_gate_root(repo_cwd, "main", "gate-")

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
        patch("issuesmith.ac_contract.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.ac_contract._git",
            side_effect=[fetch_proc, add_fail, add_ok],
        ),
        patch("issuesmith.ac_contract.subprocess.run") as mock_run,
        patch("issuesmith.ac_contract.shutil.rmtree"),
        patch("issuesmith.ac_contract.time.sleep"),
    ):
        result = materialize_gate_root(repo_cwd, "main", "gate-")

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
        patch("issuesmith.ac_contract.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.ac_contract._git",
            side_effect=[fetch_proc, add_fail, add_ok],
        ),
        patch("issuesmith.ac_contract.subprocess.run"),
        patch("issuesmith.ac_contract.shutil.rmtree") as mock_rmtree,
        patch("issuesmith.ac_contract.time.sleep") as mock_sleep,
    ):
        result = materialize_gate_root(repo_cwd, "main", "gate-")

    assert result == gate_root
    mock_rmtree.assert_called_once_with(gate_root, ignore_errors=True)
    mock_sleep.assert_called_once_with(5)


def test_three_consecutive_add_failures_raise(repo_cwd: Path, gate_root: Path) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, "still locked")

    with (
        patch("issuesmith.ac_contract.tempfile.mkdtemp", return_value=str(gate_root)),
        patch(
            "issuesmith.ac_contract._git",
            side_effect=[fetch_proc, add_fail, add_fail, add_fail],
        ),
        patch("issuesmith.ac_contract.subprocess.run"),
        patch("issuesmith.ac_contract.shutil.rmtree") as mock_rmtree,
        patch("issuesmith.ac_contract.time.sleep") as mock_sleep,
    ):
        with pytest.raises(GateMaterializationError):
            materialize_gate_root(repo_cwd, "main", "gate-")

    assert mock_rmtree.call_count == 3
    assert mock_sleep.call_args_list == [call(5), call(10)]


def test_dual_gate_roots_cleans_up_on_success(repo_cwd: Path, gate_root: Path) -> None:
    secondary = gate_root / "secondary"
    secondary.mkdir()
    primary = gate_root / "primary"
    primary.mkdir()

    with (
        patch(
            "issuesmith.ac_contract.materialize_gate_root",
            side_effect=[primary, secondary],
        ) as mock_materialize,
        patch("issuesmith.ac_contract.cleanup_gate_root") as mock_cleanup,
    ):
        with dual_gate_roots(repo_cwd, repo_cwd, "main") as roots:
            assert roots == (primary, secondary)
        assert mock_materialize.call_count == 2
        assert mock_cleanup.call_args_list == [
            call(repo_cwd, primary),
            call(repo_cwd, secondary),
        ]


def test_dual_gate_roots_cleans_up_primary_when_secondary_materialize_fails(
    repo_cwd: Path, gate_root: Path
) -> None:
    primary = gate_root / "primary"
    primary.mkdir()

    with (
        patch("issuesmith.ac_contract.materialize_gate_root", side_effect=[primary, GateMaterializationError("boom")]),
        patch("issuesmith.ac_contract.cleanup_gate_root") as mock_cleanup,
    ):
        with pytest.raises(GateMaterializationError, match="boom"):
            with dual_gate_roots(repo_cwd, repo_cwd, "main"):
                pass
    mock_cleanup.assert_called_once_with(repo_cwd, primary)


def test_dual_gate_roots_cleans_up_on_exception(repo_cwd: Path, gate_root: Path) -> None:
    secondary = gate_root / "secondary"
    secondary.mkdir()
    primary = gate_root / "primary"
    primary.mkdir()

    with (
        patch(
            "issuesmith.ac_contract.materialize_gate_root",
            side_effect=[primary, secondary],
        ),
        patch("issuesmith.ac_contract.cleanup_gate_root") as mock_cleanup,
    ):
        with pytest.raises(RuntimeError, match="check failed"):
            with dual_gate_roots(repo_cwd, repo_cwd, "main"):
                raise RuntimeError("check failed")
    assert mock_cleanup.call_args_list == [
        call(repo_cwd, primary),
        call(repo_cwd, secondary),
    ]


def test_cleanup_gate_root_falls_back_to_rmtree(repo_cwd: Path, gate_root: Path) -> None:
    with (
        patch("issuesmith.ac_contract._git", return_value=_completed(1)),
        patch("issuesmith.ac_contract.shutil.rmtree") as mock_rmtree,
    ):
        cleanup_gate_root(repo_cwd, gate_root)
    mock_rmtree.assert_called_once_with(gate_root, ignore_errors=True)
