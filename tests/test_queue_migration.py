"""tests/tools/issuesmith/test_queue_migration.py — v1 state migration + seed idempotency."""
from __future__ import annotations

import json
from pathlib import Path

from issuesmith.queue_store import QueueStore

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "issuesmith"


class TestV1StateMigration:
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
