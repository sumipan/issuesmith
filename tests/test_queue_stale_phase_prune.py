"""Queue tick prunes lines whose phase was removed from config. refs #7836."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore

_NOW = "2026-01-01T12:00:00+00:00"
_RID_A = "00000000-0000-4000-8000-000000000101"
_RID_B = "00000000-0000-4000-8000-000000000102"
_RID_ACTIVE = "00000000-0000-4000-8000-000000000201"
_RID_IN_FLIGHT = "00000000-0000-4000-8000-000000000202"


def _phases_without_merge() -> list[dict[str, object]]:
    return [
        {
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "preconditions": [],
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "implementation",
            "entry_step": "cp2",
            "handler": "impl",
            "preconditions": ["draft-done"],
            "advance_when": ["deps_terminal"],
        },
    ]


@pytest.fixture
def no_merge_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/app", "phases": _phases_without_merge()}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _store(tmp_path: Path) -> QueueStore:
    base = tmp_path / "queue-dir"
    return QueueStore(
        queue_path=base / "queue.jsonl",
        state_path=base / "state.json",
        lock_path=base / "lock",
    )


def _queue_line(
    *,
    request_id: str,
    phase: str,
    issue: int = 99,
    actor_kind: str = "human",
) -> str:
    return json.dumps(
        {
            "request_id": request_id,
            "issue": issue,
            "phase": phase,
            "source": "test",
            "actor_kind": actor_kind,
            "priority": "normal",
            "requested_at": _NOW,
            "requested_by": ["tester"],
        },
        ensure_ascii=False,
    )


def _write_state(
    path: Path,
    *,
    active_order: list[str] | None = None,
    in_flight: list[dict[str, object]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "revision": 0,
        "active_order": list(active_order or []),
        "completed_request_ids": [],
        "last_triaged_revision": 0,
        "blocks": [],
        "in_flight": list(in_flight or []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_prune_moves_unknown_phase_lines_to_archive(
    tmp_path: Path, no_merge_config: None
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _queue_line(request_id=_RID_A, phase="merge"),
        _queue_line(request_id=_RID_B, phase="merge", issue=100),
    ]
    store.queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = store.prune_unknown_phase_requests()

    assert result == [(_RID_A, "merge"), (_RID_B, "merge")]
    assert store.queue_path.read_text(encoding="utf-8") == ""
    archive = store.queue_path.with_name("queue.pruned.jsonl")
    archived = archive.read_text(encoding="utf-8").strip().splitlines()
    assert len(archived) == 2
    assert json.loads(archived[0])["request_id"] == _RID_A
    assert json.loads(archived[1])["request_id"] == _RID_B


def test_prune_keeps_active_and_in_flight_requests(
    tmp_path: Path, no_merge_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _queue_line(request_id=_RID_ACTIVE, phase="merge", issue=10),
        _queue_line(request_id=_RID_IN_FLIGHT, phase="merge", issue=20),
    ]
    original = "\n".join(lines) + "\n"
    store.queue_path.write_text(original, encoding="utf-8")
    _write_state(
        store.state_path,
        active_order=[_RID_ACTIVE],
        in_flight=[{"issue": 20, "engine": "cursor"}],
    )

    result = store.prune_unknown_phase_requests()

    assert result == []
    assert store.queue_path.read_text(encoding="utf-8") == original
    assert not store.queue_path.with_name("queue.pruned.jsonl").exists()
    err = capsys.readouterr().err
    assert f"warning: not pruning stale phase queue request {_RID_ACTIVE}" in err
    assert "active_order" in err
    assert f"warning: not pruning stale phase queue request {_RID_IN_FLIGHT}" in err
    assert "in_flight" in err


def test_prune_keeps_other_invalid_lines(tmp_path: Path, no_merge_config: None) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "{not json",
        json.dumps({"request_id": _RID_A, "issue": 1, "source": "x"}),
        _queue_line(request_id=_RID_B, phase="merge", actor_kind="bot"),
    ]
    original = "\n".join(lines) + "\n"
    store.queue_path.write_text(original, encoding="utf-8")

    result = store.prune_unknown_phase_requests()

    assert result == []
    assert store.queue_path.read_text(encoding="utf-8") == original
    assert not store.queue_path.with_name("queue.pruned.jsonl").exists()


def test_prune_dry_run_does_not_write_files(tmp_path: Path, no_merge_config: None) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    content = _queue_line(request_id=_RID_A, phase="merge") + "\n"
    store.queue_path.write_text(content, encoding="utf-8")
    _write_state(store.state_path)
    queue_mtime = os.path.getmtime(store.queue_path)
    state_mtime = os.path.getmtime(store.state_path)

    result = store.prune_unknown_phase_requests(dry_run=True)

    assert result == [(_RID_A, "merge")]
    assert store.queue_path.read_text(encoding="utf-8") == content
    assert os.path.getmtime(store.queue_path) == queue_mtime
    assert os.path.getmtime(store.state_path) == state_mtime
    assert not store.queue_path.with_name("queue.pruned.jsonl").exists()
    assert not any(store.queue_path.parent.glob("*.tmp.*"))


def test_read_suppresses_only_unknown_phase_warning(
    tmp_path: Path, no_merge_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _queue_line(request_id=_RID_A, phase="merge"),
        "{bad",
        _queue_line(request_id=_RID_B, phase="merge", actor_kind="bot"),
    ]
    store.queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    store.snapshot()
    captured = capsys.readouterr()

    assert captured.out == ""
    assert "warning: skipping invalid queue line 1" not in captured.err
    assert captured.err == (
        "warning: skipping malformed queue line 2\n"
        "warning: skipping invalid queue line 3\n"
    )


def test_tick_prunes_once_and_reports_sorted_phases(
    tmp_path: Path, no_merge_config: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from issuesmith import queue as qmod
    from issuesmith.queue import DispatchResult

    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _queue_line(request_id=_RID_A, phase="merge"),
        _queue_line(request_id=_RID_B, phase="retired", issue=50),
    ]
    store.queue_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def fake_dispatch_one(**_kwargs: object) -> DispatchResult:
        return DispatchResult(False, reason="empty queue")

    monkeypatch.setattr(qmod, "dispatch_one", fake_dispatch_one)
    argv = [
        "--queue-path",
        str(store.queue_path),
        "--state-path",
        str(store.state_path),
        "--lock-path",
        str(store.lock_path),
        "tick",
    ]

    code = qmod.main(argv)
    first = capsys.readouterr()
    assert code == 0
    out_lines = [line for line in first.out.splitlines() if line.strip()]
    assert out_lines[0] == (
        "queue: pruned 2 request(s) with unknown phase: merge, retired"
    )
    assert out_lines[-1] == "no dispatch: empty queue"
    assert first.err == ""

    code = qmod.main(argv)
    second = capsys.readouterr()
    assert code == 0
    assert "queue: pruned" not in second.out
    assert second.out.strip() == "no dispatch: empty queue"
