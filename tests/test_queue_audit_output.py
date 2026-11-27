"""``queue audit --json`` output (refs #8141)."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from issuesmith.queue_store import QueueStore


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _audit_args(store: QueueStore, *, json_mode: bool) -> argparse.Namespace:
    return argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        json=json_mode,
    )


def test_audit_json_failure_is_single_object(tmp_path, capsys) -> None:
    """refs #8141: audit --json reports errors as one object with exit 1."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    rid = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="human",
        priority="normal",
        requested_by=["x"],
        requested_at="2026-09-11T06:00:00+00:00",
    ).request_id
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    state["active_order"] = [rid]
    state["completed_request_ids"] = [rid]
    store.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    code = qmod._cmd_audit(_audit_args(store, json_mode=True))
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload == {
        "ok": False,
        "purged": [],
        "errors": ["active_order intersects completed_request_ids"],
    }
    assert code == 1
    assert "AUDIT" not in out


def test_audit_json_reports_purged_ids(tmp_path, capsys) -> None:
    """refs #8141: audit --json lists purged orphan ids when healthy."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    rid = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="normal",
        requested_by=["a"],
        requested_at="2026-09-11T06:00:00+00:00",
    ).request_id
    orphan = str(uuid.uuid4())
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    state["active_order"] = [orphan, rid]
    store.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    code = qmod._cmd_audit(_audit_args(store, json_mode=True))
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["purged"] == [orphan]
    assert payload["errors"] == []
    assert code == 0
    assert orphan not in json.loads(store.state_path.read_text())["active_order"]


def test_audit_text_output_is_unchanged(tmp_path, capsys) -> None:
    """refs #8141: audit without --json keeps AUDIT OK / AUDIT FAIL lines."""
    from issuesmith import queue as qmod

    healthy = _store(tmp_path / "healthy")
    orphan = str(uuid.uuid4())
    rid = healthy.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="normal",
        requested_by=["a"],
        requested_at="2026-09-11T06:00:00+00:00",
    ).request_id
    state = json.loads(healthy.state_path.read_text(encoding="utf-8"))
    state["active_order"] = [orphan, rid]
    healthy.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    assert qmod._cmd_audit(_audit_args(healthy, json_mode=False)) == 0
    out_ok = capsys.readouterr().out
    assert "AUDIT OK" in out_ok
    assert "purged orphan" in out_ok

    fail_store = _store(tmp_path / "fail")
    fail_rid = fail_store.enqueue(
        issue=2,
        phase="draft",
        source="s",
        actor_kind="human",
        priority="normal",
        requested_by=["x"],
        requested_at="2026-09-11T06:00:00+00:00",
    ).request_id
    fail_state = json.loads(fail_store.state_path.read_text(encoding="utf-8"))
    fail_state["active_order"] = [fail_rid]
    fail_state["completed_request_ids"] = [fail_rid]
    fail_store.state_path.write_text(json.dumps(fail_state, indent=2) + "\n", encoding="utf-8")
    assert qmod._cmd_audit(_audit_args(fail_store, json_mode=False)) == 1
    out_fail = capsys.readouterr().out
    assert out_fail.startswith("AUDIT FAIL:")
