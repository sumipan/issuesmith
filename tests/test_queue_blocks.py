"""Queue block persistence, matching, unblock, and status."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from issuesmith import queue as qmod
from issuesmith.queue_store import QueueBlock, QueueStore, QueueValidationError

_NOW = datetime.now(timezone.utc)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


class TestQueueBlockStore:
    def test_legacy_halt_converts_to_single_block(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "halt": True,
                    "halt_scope": "phase:develop",
                    "halt_reason": "main red",
                    "halt_event": "main_red",
                }
            ),
            encoding="utf-8",
        )
        store = _store(tmp_path)
        snap = store.snapshot()
        assert len(snap.blocks) == 1
        block = snap.blocks[0]
        assert block.scope == "phase:develop"
        assert block.kind == "main_red"
        assert block.reason == "main red"
        assert block.set_by == "legacy_state"

    def test_halt_false_yields_empty_blocks(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"halt": False}), encoding="utf-8")
        store = _store(tmp_path)
        assert store.snapshot().blocks == ()
        assert store.snapshot().blocks == ()

    def test_save_drops_legacy_halt_fields(self, tmp_path):
        store = _store(tmp_path)
        store.add_block(
            QueueBlock(
                scope="all",
                kind="manual",
                reason="hold",
                until=None,
                set_by="test",
            )
        )
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert "halt" not in state
        assert state["blocks"][0]["kind"] == "manual"

    def test_add_block_replaces_same_scope_kind(self, tmp_path):
        store = _store(tmp_path)
        b1 = QueueBlock("all", "main_red", "first", None, "a")
        b2 = QueueBlock("all", "main_red", "second", None, "b")
        assert store.add_block(b1) is True
        assert store.add_block(b2) is True
        assert len(store.snapshot().blocks) == 1
        assert store.snapshot().blocks[0].reason == "second"

    def test_until_in_past_is_ignored(self, tmp_path):
        store = _store(tmp_path)
        past = (_NOW - timedelta(minutes=1)).isoformat()
        store.add_block(QueueBlock("all", "t", "expired", past, "test"))
        assert store.snapshot().blocks == ()

    def test_invalid_scope_raises(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(QueueValidationError):
            store.add_block(QueueBlock("bogus", "k", "", None, "test"))

    def test_invalid_until_raises(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(QueueValidationError):
            store.add_block(QueueBlock("all", "k", "", "2026-01-01T00:00:00", "test"))


class TestQueueUnblockCLI:
    def test_unblock_by_kind(self, tmp_path, capsys):
        store = _store(tmp_path)
        store.add_block(QueueBlock("all", "main_red", "r", None, "t"))
        store.add_block(QueueBlock("all", "systemic_step_failure", "s", None, "t"))
        code = qmod.main(
            [
                "--queue-path",
                str(store.queue_path),
                "--state-path",
                str(store.state_path),
                "--lock-path",
                str(store.lock_path),
                "unblock",
                "--kind",
                "main_red",
            ]
        )
        assert code == 0
        kinds = {b.kind for b in store.snapshot().blocks}
        assert kinds == {"systemic_step_failure"}
        assert "removed 1 block" in capsys.readouterr().out

    def test_unblock_idempotent_zero_removed(self, tmp_path, capsys):
        store = _store(tmp_path)
        code = qmod.main(
            [
                "--queue-path",
                str(store.queue_path),
                "--state-path",
                str(store.state_path),
                "--lock-path",
                str(store.lock_path),
                "unblock",
                "--kind",
                "main_red",
            ]
        )
        assert code == 0
        assert "removed 0 block" in capsys.readouterr().out


class TestQueueStatusBlocks:
    def test_status_json_includes_blocks(self, tmp_path, capsys):
        store = _store(tmp_path)
        store.add_block(
            QueueBlock(
                scope="repo:sumipan/issuesmith",
                kind="manual",
                reason="hold",
                until=None,
                set_by="ops",
            )
        )
        code = qmod.main(
            [
                "--queue-path",
                str(store.queue_path),
                "--state-path",
                str(store.state_path),
                "--lock-path",
                str(store.lock_path),
                "status",
                "--json",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["blocks"][0]["scope"] == "repo:sumipan/issuesmith"
        assert payload["blocks"][0]["set_by"] == "ops"
