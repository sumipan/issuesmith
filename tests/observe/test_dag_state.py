"""Tests for issuesmith.observe.dag_state.load_dag_states."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from issuesmith.observe.dag_state import load_dag_states


def _write_exec(exec_path: Path, rows: list[dict]) -> None:
    exec_path.write_text('\n'.join((json.dumps(r) for r in rows)) + '\n', encoding='utf-8')

def _write_done(done_dir: Path, uuid: str, content: str) -> None:
    (done_dir / uuid).write_text(content, encoding='utf-8')

def _write_running(running_dir: Path, uuid: str) -> None:
    (running_dir / f'{uuid}.json').write_text('{}', encoding='utf-8')

@pytest.fixture
def dirs(tmp_path: Path):
    exec_path = tmp_path / 'exec.jsonl'
    done_dir = tmp_path / 'done'
    done_dir.mkdir()
    running_dir = tmp_path / 'running'
    running_dir.mkdir()
    return (exec_path, done_dir, running_dir)
_DAG_A_P1 = {'uuid': 'p1', 'idempotency_key': 'issuesmith:impl:101', 'depends': []}
_DAG_A_P2 = {'uuid': 'p2', 'idempotency_key': 'issuesmith:impl:101', 'depends': ['p1']}

def test_unmarked_second_step_gives_pending(dirs):
    """First step success with no marker on second step yields pending."""
    exec_path, done_dir, running_dir = dirs
    _write_exec(exec_path, [_DAG_A_P1, _DAG_A_P2])
    _write_done(done_dir, 'p1', '0')
    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == 'pending'

def test_p2_done_deferred_reads_as_failed(dirs):
    """Legacy done marker DEFERRED is read as failed like other unknown markers."""
    exec_path, done_dir, running_dir = dirs
    p2_with_name = {**_DAG_A_P2, 'annotations': {'step_name': 'p2'}}
    _write_exec(exec_path, [_DAG_A_P1, p2_with_name])
    _write_done(done_dir, 'p1', '0')
    _write_done(done_dir, 'p2', 'DEFERRED')
    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == 'failed'
    assert states[101].failed_step == 'p2'
