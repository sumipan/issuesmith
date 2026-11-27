"""AC-1 / AC-2 / AC-5 / AC-6: deterministic-first triage, CAS retry, orphan purge (#3159)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore

_JST = ZoneInfo("Asia/Tokyo")
_FIXED_NOW = datetime(2026, 9, 11, 6, 0, tzinfo=_JST)
_NOW = _FIXED_NOW.isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "src/**"\n'
    "```\n"
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _write_minimal_config(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "supported_repos": ["sumipan/nexus"],
                "concurrency": {"default": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_purge_orphan_ids_and_doctor_audit(tmp_path, monkeypatch, capsys):
    """AC-5: doctor detects orphan IDs and audit purges them."""
    _write_minimal_config(tmp_path, monkeypatch)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="normal",
        requested_by=["a"],
        requested_at=_NOW,
    )
    orphan = str(uuid.uuid4())
    # Inject orphan into raw state (snapshot() filters these out).
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    state["active_order"] = [orphan, r.request_id]
    store.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    orphans = store.purge_orphan_ids()
    assert orphans == [orphan]
    state2 = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert orphan not in state2["active_order"]
    assert r.request_id in state2["active_order"]

    # Re-inject for doctor detection
    state2["active_order"] = [orphan, r.request_id]
    store.state_path.write_text(json.dumps(state2, indent=2) + "\n", encoding="utf-8")

    ns = type("NS", (), {})()
    ns.queue_path = str(store.queue_path)
    ns.state_path = str(store.state_path)
    ns.lock_path = str(store.lock_path)
    code = qmod._cmd_doctor(ns)
    assert code == 1
    out = capsys.readouterr().out
    assert orphan in out

    # audit should purge
    ns.offline = True
    code = qmod._cmd_audit(ns)
    assert code == 0
    state3 = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert orphan not in state3["active_order"]
    out2 = capsys.readouterr().out
    assert orphan in out2 or "orphan" in out2.lower() or "purged" in out2.lower()


