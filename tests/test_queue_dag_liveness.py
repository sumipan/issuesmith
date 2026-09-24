"""Tests for DAG liveness checks in queue._find_untracked_running and _dispatch_pipeline_ready (AC-5, AC-6)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issuesmith.queue_store import QueueStore

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


def _make_done_marker(done_dir: Path, uuid: str, content: str = "1") -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / uuid).write_text(content, encoding="utf-8")


class _ListIssuesClient:
    def __init__(self, running_issues: list[int]) -> None:
        self._running = running_issues

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        if "develop-running" in label:
            return [{"number": n} for n in self._running]
        return []

    def issue_get(self, number: int, fields=None):
        return {"number": number, "state": "OPEN", "labels": []}


def test_ac5_failed_dag_not_reregistered(tmp_path, monkeypatch):
    """AC-5: develop-running open issue with failed DAG is NOT re-registered into in_flight."""
    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    running_dir = tmp_path / "running"
    done_dir.mkdir()
    running_dir.mkdir()

    _write_exec(exec_path, "impl-102", 102)
    _make_done_marker(done_dir, "impl-102", "1")

    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    store = _store(tmp_path)
    snap = store.snapshot()
    assert snap.in_flight == []

    client = _ListIssuesClient([102])
    result = qmod._find_untracked_running(client, snap)
    assert result == []

    recovered = qmod._recover_untracked_in_flight(client, store, snap)
    assert recovered == 0
    assert store.snapshot().in_flight == []


def test_ac5_running_dag_is_reregistered(tmp_path, monkeypatch):
    """AC-5: develop-running open issue with running DAG IS re-registered into in_flight."""
    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    running_dir = tmp_path / "running"
    done_dir.mkdir()
    running_dir.mkdir()

    _write_exec(exec_path, "impl-103", 103)
    _make_running_marker(running_dir, "impl-103")

    engine_state = tmp_path / "engine.yml"
    engine_state.write_text(
        "design:\n  engine: claude\nimplementation:\n  engine: claude\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_state)

    store = _store(tmp_path)
    snap = store.snapshot()

    class ClientWithBody:
        def list_issues(self, label, state="open"):
            if "develop-running" in label:
                return [{"number": 103}]
            return []

        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "body": (
                    "```yaml\ntarget_repo: sumipan/nexus\n"
                    "base_branch: main\nallow_paths:\n  - \"tools/**\"\n```\n"
                ),
                "labels": [{"name": "issuesmith:develop-running"}],
            }

    result = qmod._find_untracked_running(ClientWithBody(), snap)
    assert result == [103]

    recovered = qmod._recover_untracked_in_flight(ClientWithBody(), store, snap)
    assert recovered == 1
    snap2 = store.snapshot()
    assert any(e["issue"] == 103 for e in snap2.in_flight)


def test_ac6_running_dag_does_not_block_dispatch_gate(tmp_path, monkeypatch):
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


def test_ac6_orphan_pending_exec_blocks_dispatch_gate(tmp_path, monkeypatch):
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


def test_failed_dag_issue_no_longer_untracked_warning(tmp_path, monkeypatch):
    """Dead DAG issues are excluded from 'untracked running' so no false warnings."""
    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    running_dir = tmp_path / "running"
    done_dir.mkdir()
    running_dir.mkdir()

    _write_exec(exec_path, "impl-200", 200)
    _make_done_marker(done_dir, "impl-200", "ENGINE_ERROR")

    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    store = _store(tmp_path)
    snap = store.snapshot()

    result = qmod._find_untracked_running(_ListIssuesClient([200]), snap)
    assert 200 not in result
