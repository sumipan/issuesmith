"""Tests for dual-root gate materialize/cleanup helpers (#2919 / #4275)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.ac_contract import GateMaterializationError, cleanup_gate_root, materialize_gate_root


def _completed(rc: int, stderr: str='') -> MagicMock:
    proc = MagicMock()
    proc.returncode = rc
    proc.stderr = stderr
    return proc

@pytest.fixture
def repo_cwd(tmp_path: Path) -> Path:
    repo = tmp_path / 'repo'
    repo.mkdir()
    return repo

@pytest.fixture
def gate_root(tmp_path: Path) -> Path:
    root = tmp_path / 'gate-root'
    root.mkdir()
    return root

def test_worktree_add_failure_includes_stderr_after_max_retries(repo_cwd: Path, gate_root: Path) -> None:
    fetch_proc = _completed(0)
    add_fail = _completed(1, 'lock file exists')
    with patch('issuesmith.ac_contract.tempfile.mkdtemp', return_value=str(gate_root)), patch('issuesmith.ac_contract._git', side_effect=[fetch_proc, add_fail, add_fail, add_fail]), patch('issuesmith.ac_contract.subprocess.run') as mock_run, patch('issuesmith.ac_contract.shutil.rmtree') as mock_rmtree, patch('issuesmith.ac_contract.time.sleep'):
        with pytest.raises(GateMaterializationError, match='after 3 attempts') as exc_info:
            materialize_gate_root(repo_cwd, 'main', 'gate-')
    msg = str(exc_info.value)
    assert 'add_rc=1' in msg
    assert "add_stderr='lock file exists'" in msg
    assert mock_run.call_count == 3
    assert mock_rmtree.call_count == 3

def test_cleanup_gate_root_falls_back_to_rmtree(repo_cwd: Path, gate_root: Path) -> None:
    with patch('issuesmith.ac_contract._git', return_value=_completed(1)), patch('issuesmith.ac_contract.shutil.rmtree') as mock_rmtree:
        cleanup_gate_root(repo_cwd, gate_root)
    mock_rmtree.assert_called_once_with(gate_root, ignore_errors=True)
