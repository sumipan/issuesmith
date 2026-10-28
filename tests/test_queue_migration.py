"""tests/tools/issuesmith/test_queue_migration.py — v1 state migration + seed idempotency."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issuesmith.queue_store import QueueStore, default_state

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "issuesmith"
_NOW = datetime.now(timezone.utc).isoformat()


class TestV1StateMigration:
    def test_v1_state_loads_as_empty_queue(self, tmp_path):
        """Old night-queue-state-v1.json (cursor/last_issue/halt) loads cleanly."""
        v1_path = FIXTURE_DIR / "night-queue-state-v1.json"
        assert v1_path.exists(), f"fixture not found: {v1_path}"

        state_path = tmp_path / "state.json"
        state_path.write_text(v1_path.read_text(encoding="utf-8"), encoding="utf-8")

        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=state_path,
            lock_path=tmp_path / "lock",
        )
        snap = store.snapshot()
        assert snap.active_order == []
        assert snap.halt is False
        assert snap.last_issue is None

    def test_v1_state_preserves_halt(self, tmp_path):
        v1 = {"cursor": 2, "last_issue": 999, "halt": True, "halt_reason": "stopped"}
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(v1), encoding="utf-8")

        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=state_path,
            lock_path=tmp_path / "lock",
        )
        snap = store.snapshot()
        assert snap.halt is True
        assert snap.halt_reason == "stopped"

    def test_enqueue_after_v1_migration_works(self, tmp_path):
        """Can enqueue into a store that started from v1 state."""
        v1 = {"cursor": 0, "last_issue": None, "halt": False, "halt_reason": None}
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(v1), encoding="utf-8")

        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=state_path,
            lock_path=tmp_path / "lock",
        )
        result = store.enqueue(
            issue=42, phase="draft", source="test", actor_kind="human",
            priority="normal", requested_by=["user"], requested_at=_NOW,
        )
        assert result.created is True
        snap = store.snapshot()
        assert len(snap.active_order) == 1

    def test_milestone_chains_missing_defaults_empty(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps({"schema_version": 1, "revision": 0}), encoding="utf-8")
        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=state_path,
            lock_path=tmp_path / "lock",
        )
        snap = store.snapshot()
        assert snap.milestone_chains == {}


class TestSeedIdempotent:
    def test_seed_is_idempotent_across_restarts(self, tmp_path):
        """Seed entries with the same seed_key are not duplicated on re-enqueue."""
        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=tmp_path / "state.json",
            lock_path=tmp_path / "lock",
        )
        r1 = store.enqueue(
            issue=100, phase="draft", source="night-queue", actor_kind="automation",
            priority="low", requested_by=["nq"], requested_at=_NOW,
            seed_key="nq:100:draft",
        )
        assert r1.created is True

        store2 = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=tmp_path / "state.json",
            lock_path=tmp_path / "lock",
        )
        r2 = store2.enqueue(
            issue=100, phase="draft", source="night-queue", actor_kind="automation",
            priority="low", requested_by=["nq"], requested_at=_NOW,
            seed_key="nq:100:draft",
        )
        assert r2.created is False

        snap = store2.snapshot()
        assert len(snap.active_order) == 1

    def test_different_seed_keys_create_separate(self, tmp_path):
        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=tmp_path / "state.json",
            lock_path=tmp_path / "lock",
        )
        r1 = store.enqueue(
            issue=100, phase="draft", source="nq", actor_kind="automation",
            priority="low", requested_by=["nq"], requested_at=_NOW,
            seed_key="nq:100:draft",
        )
        r2 = store.enqueue(
            issue=200, phase="draft", source="nq", actor_kind="automation",
            priority="low", requested_by=["nq"], requested_at=_NOW,
            seed_key="nq:200:draft",
        )
        assert r1.created is True
        assert r2.created is True
        snap = store.snapshot()
        assert len(snap.active_order) == 2


class TestDefaultState:
    def test_default_state_shape(self):
        state = default_state()
        assert state["schema_version"] == 1
        assert state["revision"] == 0
        assert state["active_order"] == []
        assert state["halt"] is False
        assert state["last_issue"] is None


class TestQueueMigrateLastIssue:
    def test_migrate_dry_run_ignores_last_issue(self, tmp_path, capsys):
        from issuesmith import queue as qmod

        night = tmp_path / "night.json"
        night.write_text(
            json.dumps(
                {
                    "cursor": 0,
                    "last_issue": 999,
                    "halt": True,
                    "halt_reason": "stopped",
                }
            ),
            encoding="utf-8",
        )
        seed = tmp_path / "seed.jsonl"
        seed.write_text("", encoding="utf-8")
        code = qmod.main(
            [
                "--queue-path",
                str(tmp_path / "queue.jsonl"),
                "--state-path",
                str(tmp_path / "state.json"),
                "--lock-path",
                str(tmp_path / "lock"),
                "migrate",
                "--from-night-queue-state",
                str(night),
                "--seed",
                str(seed),
                "--dry-run",
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        import_action = payload["planned"][0]
        assert "last_issue" not in import_action
        assert import_action["halt"] is True

    def test_migrate_applies_halt_not_last_issue(self, tmp_path, capsys):
        from issuesmith import queue as qmod

        night = tmp_path / "night.json"
        night.write_text(
            json.dumps(
                {
                    "cursor": 0,
                    "last_issue": 999,
                    "halt": True,
                    "halt_reason": "stopped",
                }
            ),
            encoding="utf-8",
        )
        seed = tmp_path / "seed.jsonl"
        seed.write_text("", encoding="utf-8")
        code = qmod.main(
            [
                "--queue-path",
                str(tmp_path / "queue.jsonl"),
                "--state-path",
                str(tmp_path / "state.json"),
                "--lock-path",
                str(tmp_path / "lock"),
                "migrate",
                "--from-night-queue-state",
                str(night),
                "--seed",
                str(seed),
            ]
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert "last_issue" not in payload
        store = QueueStore(
            queue_path=tmp_path / "queue.jsonl",
            state_path=tmp_path / "state.json",
            lock_path=tmp_path / "lock",
        )
        snap = store.snapshot()
        assert snap.halt is True
        assert snap.halt_reason == "stopped"
        assert snap.last_issue is None
