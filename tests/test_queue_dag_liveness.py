"""Tests for workflow-scoped idle gate and DAG liveness in _dispatch_pipeline_ready."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore
from tests.conftest import NEXUS_TEST_PHASES

_NOW = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _write_exec(path: Path, uuid: str, issue: int, key: str | None = None) -> None:
    if key is None:
        key = f"issuesmith:impl:{issue}"
    path.write_text(
        json.dumps({"uuid": uuid, "idempotency_key": key, "depends": []}) + "\n",
        encoding="utf-8",
    )


def _make_running_marker(running_dir: Path, uuid: str) -> None:
    running_dir.mkdir(parents=True, exist_ok=True)
    (running_dir / f"{uuid}.json").write_text("{}", encoding="utf-8")


def _use_label_namespace(tmp_path: Path, monkeypatch, namespace: str) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "owner/repo",
                "label_namespace": namespace,
                "phases": NEXUS_TEST_PHASES,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_other_workflow_pending_dag_does_not_block_namespaced_queue(
    tmp_path, monkeypatch
):
    """AC-1: a wsl queue ignores another workflow's unfinished, non-running exec rows."""
    from issuesmith import queue as qmod

    _use_label_namespace(tmp_path, monkeypatch, "wsl")
    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()

    _write_exec(exec_path, "m-issuesmith", 7471, key="issuesmith:impl:7471")
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    snap = _store(tmp_path).snapshot()
    assert snap.in_flight == []

    assert qmod._dispatch_pipeline_ready(snap, 1, _NOW) is True


def test_own_workflow_pending_dag_blocks_namespaced_queue(tmp_path, monkeypatch):
    """AC-2: a wsl queue still blocks on its own unfinished, non-running exec rows."""
    from issuesmith import queue as qmod

    _use_label_namespace(tmp_path, monkeypatch, "wsl")
    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()

    _write_exec(exec_path, "m-wsl", 7600, key="wsl:impl:7600")
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    snap = _store(tmp_path).snapshot()
    assert snap.in_flight == []

    assert qmod._dispatch_pipeline_ready(snap, 1, _NOW) is False


def test_default_namespace_queue_still_blocks_on_issuesmith_pending_dag(
    tmp_path, monkeypatch
):
    """AC-3: default issuesmith namespace blocks on its own rows and ignores wsl rows."""
    from issuesmith import queue as qmod

    _use_label_namespace(tmp_path, monkeypatch, "issuesmith")
    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()

    exec_path.write_text(
        json.dumps(
            {"uuid": "m-issuesmith", "idempotency_key": "issuesmith:impl:7471", "depends": []}
        )
        + "\n"
        + json.dumps({"uuid": "m-wsl", "idempotency_key": "wsl:impl:7600", "depends": []})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    snap = _store(tmp_path).snapshot()
    assert snap.in_flight == []

    assert qmod._dispatch_pipeline_ready(snap, 1, _NOW) is False
    assert qmod._iter_issuesmith_exec_records() == [("m-issuesmith", 7471)]


class _ListIssuesClient:
    def __init__(self, running_issues: list[int]) -> None:
        self._running = running_issues

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        if "develop-running" in label:
            return [{"number": n} for n in self._running]
        return []

    def issue_get(self, number: int, fields=None):
        return {"number": number, "state": "OPEN", "labels": []}


def test_running_dag_does_not_block_dispatch_gate(tmp_path, monkeypatch):
    """AC-6: in_flight-absent issue with running DAG does NOT block _dispatch_pipeline_ready."""
    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    running_dir = tmp_path / "running"
    done_dir.mkdir()
    running_dir.mkdir()

    _write_exec(exec_path, "m1", 103)
    _make_running_marker(running_dir, "m1")

    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    store = _store(tmp_path)
    snap = store.snapshot()
    assert snap.in_flight == []

    result = qmod._dispatch_pipeline_ready(snap, 1, _NOW)
    assert result is True


def test_orphan_pending_exec_blocks_dispatch_gate(tmp_path, monkeypatch):
    """AC-6: in_flight-absent issue with no running marker (orphan) still blocks the gate."""
    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    running_dir = tmp_path / "running"
    done_dir.mkdir()
    running_dir.mkdir()

    _write_exec(exec_path, "m1", 103)

    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    store = _store(tmp_path)
    snap = store.snapshot()
    assert snap.in_flight == []

    result = qmod._dispatch_pipeline_ready(snap, 1, _NOW)
    assert result is False


