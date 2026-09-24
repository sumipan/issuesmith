"""Tests for issuesmith.observe.dag_state.load_dag_states (AC-4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from issuesmith.observe.dag_state import load_dag_states


def _write_exec(exec_path: Path, rows: list[dict]) -> None:
    exec_path.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n",
        encoding="utf-8",
    )


def _write_done(done_dir: Path, uuid: str, content: str) -> None:
    (done_dir / uuid).write_text(content, encoding="utf-8")


def _write_running(running_dir: Path, uuid: str) -> None:
    (running_dir / f"{uuid}.json").write_text("{}", encoding="utf-8")


@pytest.fixture
def dirs(tmp_path: Path):
    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    running_dir = tmp_path / "running"
    running_dir.mkdir()
    return exec_path, done_dir, running_dir


_DAG_A_P1 = {"uuid": "p1", "idempotency_key": "issuesmith:impl:101", "depends": []}
_DAG_A_P2 = {"uuid": "p2", "idempotency_key": "issuesmith:impl:101", "depends": ["p1"]}


def test_ac4a_p1_running(dirs):
    """AC-4a: p1 running -> DagState.status == 'running'."""
    exec_path, done_dir, running_dir = dirs
    _write_exec(exec_path, [_DAG_A_P1, _DAG_A_P2])
    _write_running(running_dir, "p1")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert 101 in states
    assert states[101].status == "running"
    assert states[101].key == "issuesmith:impl:101"


def test_ac4b_p1_success_p2_pending(dirs):
    """AC-4b: p1 success, p2 no marker -> 'pending'."""
    exec_path, done_dir, running_dir = dirs
    _write_exec(exec_path, [_DAG_A_P1, _DAG_A_P2])
    _write_done(done_dir, "p1", "0")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == "pending"


def test_ac4c_p1_success_p2_engine_error(dirs):
    """AC-4c: p1 success, p2 done ENGINE_ERROR -> 'failed', failed_step == 'p2'."""
    exec_path, done_dir, running_dir = dirs
    p2_with_name = {**_DAG_A_P2, "annotations": {"step_name": "p2"}}
    _write_exec(exec_path, [_DAG_A_P1, p2_with_name])
    _write_done(done_dir, "p1", "0")
    _write_done(done_dir, "p2", "ENGINE_ERROR")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == "failed"
    assert states[101].failed_step == "p2"
    assert states[101].failed_uuid == "p2"


def test_ac4d_both_success(dirs):
    """AC-4d: both p1 and p2 success -> 'succeeded'."""
    exec_path, done_dir, running_dir = dirs
    _write_exec(exec_path, [_DAG_A_P1, _DAG_A_P2])
    _write_done(done_dir, "p1", "0")
    _write_done(done_dir, "p2", "0")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == "succeeded"


def test_ac4e_newer_generation_wins(dirs):
    """AC-4e: impl:101 failed then impl:101:1 running -> uses newer generation."""
    exec_path, done_dir, running_dir = dirs
    gen0_p1 = {"uuid": "g0-p1", "idempotency_key": "issuesmith:impl:101", "depends": []}
    gen1_p1 = {"uuid": "g1-p1", "idempotency_key": "issuesmith:impl:101:1", "depends": []}
    _write_exec(exec_path, [gen0_p1, gen1_p1])
    _write_done(done_dir, "g0-p1", "1")
    _write_running(running_dir, "g1-p1")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == "running"
    assert states[101].key == "issuesmith:impl:101:1"


def test_ac4f_no_exec_jsonl(tmp_path):
    """AC-4f: exec.jsonl does not exist -> empty dict."""
    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    running_dir = tmp_path / "running"
    running_dir.mkdir()

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states == {}


def test_failed_step_uses_uuid_when_no_step_name(dirs):
    """failed_step falls back to uuid when annotations.step_name is absent."""
    exec_path, done_dir, running_dir = dirs
    _write_exec(exec_path, [_DAG_A_P1, _DAG_A_P2])
    _write_done(done_dir, "p1", "0")
    _write_done(done_dir, "p2", "1")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == "failed"
    assert states[101].failed_step == "p2"
    assert states[101].failed_uuid == "p2"


def test_multiple_issues(dirs):
    """Multiple issues in exec.jsonl each get their own DagState."""
    exec_path, done_dir, running_dir = dirs
    rows = [
        {"uuid": "a1", "idempotency_key": "issuesmith:impl:200", "depends": []},
        {"uuid": "b1", "idempotency_key": "issuesmith:impl:201", "depends": []},
    ]
    _write_exec(exec_path, rows)
    _write_done(done_dir, "a1", "0")
    _write_running(running_dir, "b1")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[200].status == "succeeded"
    assert states[201].status == "running"


def test_non_issuesmith_rows_ignored(dirs):
    """Rows without issuesmith: prefix are ignored."""
    exec_path, done_dir, running_dir = dirs
    rows = [
        {"uuid": "x1", "idempotency_key": "other:task:99", "depends": []},
        {"uuid": "y1", "idempotency_key": "issuesmith:impl:300", "depends": []},
    ]
    _write_exec(exec_path, rows)
    _write_done(done_dir, "y1", "0")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert 99 not in states
    assert states[300].status == "succeeded"


def test_p1_failed_first_returned_as_failed_step(dirs):
    """AC-4: first failed step in row order is reported, not the last."""
    exec_path, done_dir, running_dir = dirs
    p1_named = {**_DAG_A_P1, "annotations": {"step_name": "p1"}}
    p2_named = {**_DAG_A_P2, "annotations": {"step_name": "p2"}}
    _write_exec(exec_path, [p1_named, p2_named])
    _write_done(done_dir, "p1", "1")
    _write_done(done_dir, "p2", "1")

    states = load_dag_states(exec_path, done_dir, running_dir)
    assert states[101].status == "failed"
    assert states[101].failed_step == "p1"
