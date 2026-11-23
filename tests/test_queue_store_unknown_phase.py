"""Queue JSONL read behavior for phases removed from config (Issue #7839)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore

_NOW = "2026-01-01T12:00:00+00:00"
_RID_UNKNOWN = "00000000-0000-4000-8000-000000000099"
_RID_VALID = "00000000-0000-4000-8000-000000000001"


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


def _write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
) -> str:
    return json.dumps(
        {
            "request_id": request_id,
            "issue": issue,
            "phase": phase,
            "source": "test",
            "actor_kind": "human",
            "priority": "normal",
            "requested_at": _NOW,
            "requested_by": ["tester"],
        },
        ensure_ascii=False,
    )


def _write_state(path: Path, *, completed: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "revision": 0,
        "active_order": list(completed or []),
        "completed_request_ids": list(completed or []),
        "last_triaged_revision": 0,
        "blocks": [],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def no_merge_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_config(tmp_path, monkeypatch)


def test_unknown_phase_warning_never_uses_stdout(
    tmp_path: Path, no_merge_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    store.queue_path.write_text(_queue_line(request_id=_RID_UNKNOWN, phase="merge") + "\n")

    store.snapshot()

    captured = capsys.readouterr()
    assert captured.out == ""


def test_completed_unknown_phase_is_skipped_silently(
    tmp_path: Path, no_merge_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    store.queue_path.write_text(_queue_line(request_id=_RID_UNKNOWN, phase="merge") + "\n")
    _write_state(store.state_path, completed=[_RID_UNKNOWN])

    snap = store.snapshot()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert _RID_UNKNOWN not in snap.requests
    assert _RID_UNKNOWN not in snap.active_order


def test_incomplete_unknown_phase_warns_once_and_keeps_valid_rows(
    tmp_path: Path, no_merge_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        _queue_line(request_id=_RID_UNKNOWN, phase="merge"),
        _queue_line(request_id=_RID_VALID, phase="draft"),
    ]
    store.queue_path.write_text("\n".join(lines) + "\n")

    snap = store.snapshot()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "warning: skipping invalid queue line 1\n"
    assert _RID_UNKNOWN not in snap.requests
    assert _RID_VALID in snap.requests
    assert snap.active_order == [_RID_VALID]


def test_malformed_queue_warning_uses_stderr(
    tmp_path: Path, no_merge_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    store = _store(tmp_path)
    store.queue_path.parent.mkdir(parents=True, exist_ok=True)
    store.queue_path.write_text("{not json\n")

    store.snapshot()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "warning: skipping malformed queue line 1\n"
