"""``queue doctor --json`` output (#6780 / parent #6701)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

from issuesmith.queue_store import QueueStore


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_queue_doctor_json(tmp_path, monkeypatch, capsys) -> None:
    """refs #6780: queue doctor --json returns a dict with ``ok`` when healthy."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)

    class FakeClient:
        def list_issues(self, label, state="open"):
            del label, state
            return []

    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        json=True,
    )
    with patch.object(qmod, "get_forge", return_value=FakeClient()):
        code = qmod._cmd_doctor(args)
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    assert "ok" in payload
    assert payload["ok"] is True
    assert code == 0
