"""Regression tests: last_issue serial gate and related CLI removed (#5691)."""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args

import pytest

from issuesmith import language
from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _write_state_with_last_issue(tmp_path: Path, last_issue: int) -> QueueStore:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 0,
                "active_order": [],
                "completed_request_ids": [],
                "last_triaged_revision": 0,
                "last_issue": last_issue,
                "halt": False,
                "halt_reason": None,
            }
        ),
        encoding="utf-8",
    )
    return _store(tmp_path)


class _TrackingClient:
    def __init__(self, issues: dict[int, dict]):
        self.issues = issues
        self.issue_get_calls: list[int] = []
        self.updates: list[tuple] = []
        self.comments: list[tuple] = []

    def issue_get(self, number, fields=None):
        self.issue_get_calls.append(number)
        data = dict(self.issues[number])
        data.setdefault("number", number)
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
        self.updates.append((number, labels_add, labels_remove))

    def issue_comment(self, number, body):
        self.comments.append((number, body))

    def get_issue_comments(self, number):
        return []

    def list_issues(self, label, state="open"):
        return []

    def pr_list(self, **kwargs):
        return []

    def api_request(self, *args, **kwargs):
        return []


def _patch_dispatch_gates(monkeypatch, qmod):
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
    monkeypatch.setattr(qmod, "_handler_key_consumed", lambda *a, **k: False)


def test_dispatch_ignores_stale_last_issue_in_state(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _write_state_with_last_issue(tmp_path, 40)
    store.enqueue(
        issue=300,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _TrackingClient(
        {
            40: {
                "state": "CLOSED",
                "title": "prev",
                "body": _VALID_BODY,
                "labels": [{"name": "scope:milestone"}],
            },
            300: {
                "state": "OPEN",
                "title": "next",
                "body": _VALID_BODY,
                "labels": [],
            },
        }
    )
    _patch_dispatch_gates(monkeypatch, qmod)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 300
    assert "previous issue not terminal" not in (result.reason or "")


def test_dispatch_does_not_fetch_last_issue(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _write_state_with_last_issue(tmp_path, 40)
    store.enqueue(
        issue=300,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )

    def boom_get(number, fields=None):
        if number == 40:
            raise RuntimeError("should not fetch last_issue")
        return {
            "number": number,
            "state": "OPEN",
            "title": "t",
            "body": _VALID_BODY,
            "labels": [],
        }

    client = _TrackingClient({300: {"state": "OPEN", "body": _VALID_BODY, "labels": []}})
    client.issue_get = boom_get  # type: ignore[method-assign]
    _patch_dispatch_gates(monkeypatch, qmod)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.reason == "dispatched"


def test_plan_has_no_last_issue_reasons(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _write_state_with_last_issue(tmp_path, 40)
    store.enqueue(
        issue=300,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _TrackingClient(
        {
            300: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            },
        }
    )
    _patch_dispatch_gates(monkeypatch, qmod)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    queue_plan = qmod.plan(store, client, now=now)
    for entry in queue_plan.entries:
        assert entry.reason not in ("last_issue_not_terminal", "last_issue_fetch_failed")
    assert "last_issue_not_terminal" not in get_args(qmod.PlanReason)
    assert "last_issue_fetch_failed" not in get_args(qmod.PlanReason)


def test_status_ignores_last_issue_in_state_file(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 1,
                "active_order": [],
                "completed_request_ids": [],
                "last_triaged_revision": 0,
                "last_issue": 5695,
                "halt": False,
            }
        ),
        encoding="utf-8",
    )
    store = _store(tmp_path)
    snap = store.snapshot()
    assert not hasattr(snap, "last_issue")

    monkeypatch.setattr(qmod, "get_forge", lambda **k: None)
    code = qmod.main(
        [
            "--queue-path",
            str(tmp_path / "queue.jsonl"),
            "--state-path",
            str(state_path),
            "--lock-path",
            str(tmp_path / "lock"),
            "status",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "last_issue" not in out

    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)
    args = argparse.Namespace(
        queue_path=str(tmp_path / "queue.jsonl"),
        state_path=str(state_path),
        lock_path=str(tmp_path / "lock"),
        json=True,
    )
    assert qmod._cmd_status(args) == 0
    payload = json.loads(captured.getvalue())
    assert set(payload) == {
        "revision",
        "active",
        "blocks",
        "halt",
        "halt_reason",
        "in_flight",
        "plan",
    }


def test_removed_cli_subcommands_exit_2(tmp_path, capsys):
    from issuesmith import queue as qmod

    base = [
        "--queue-path",
        str(tmp_path / "queue.jsonl"),
        "--state-path",
        str(tmp_path / "state.json"),
        "--lock-path",
        str(tmp_path / "lock"),
    ]
    with pytest.raises(SystemExit) as exc:
        qmod.main([*base, "skip", "--issue", "1"])
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        qmod.main([*base, "reset", "--keep-last-issue"])
    assert exc.value.code == 2

    store = _store(tmp_path)
    store.set_halt(True, "manual")
    code = qmod.main([*base, "reset"])
    assert code == 0
    assert store.snapshot().halt is False
    assert "issuesmith-queue reset: halt=false" in capsys.readouterr().out


def test_audit_offline_only(tmp_path, capsys, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)

    def boom(*a, **k):
        raise AssertionError("get_forge must not be called")

    monkeypatch.setattr(qmod, "get_forge", boom)
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    assert qmod._cmd_audit(args) == 0
    assert "AUDIT OK" in capsys.readouterr().out

    rid = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="human",
        priority="normal",
        requested_by=["x"],
        requested_at=_NOW,
    ).request_id
    with store.lock():
        state = store._load_state_unlocked()
        state["active_order"] = [rid]
        state["completed_request_ids"] = [rid]
        store._save_state_unlocked(state)
    assert qmod._cmd_audit(args) == 1
    assert "active_order intersects completed_request_ids" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc:
        qmod.main(
            [
                "--queue-path",
                str(store.queue_path),
                "--state-path",
                str(store.state_path),
                "--lock-path",
                str(store.lock_path),
                "audit",
                "--offline",
            ]
        )
    assert exc.value.code == 2


def test_legacy_halt_without_event_stays_halted(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    with store.lock():
        state = store._load_state_unlocked()
        state["halt"] = True
        state["halt_event"] = None
        state["halt_reason"] = (
            "last_issue #2852 is still OPEN while pipeline idle for >= 30 minutes"
        )
        store._save_state_unlocked(state)
    store.enqueue(
        issue=300,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _TrackingClient(
        {300: {"state": "OPEN", "body": _VALID_BODY, "labels": []}},
    )
    _patch_dispatch_gates(monkeypatch, qmod)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is False
    assert (result.reason or "").startswith("halted:")
    assert store.snapshot().halt is True


def test_migrate_imports_halt_not_last_issue(tmp_path, capsys):
    from issuesmith import queue as qmod

    night = tmp_path / "night.json"
    night.write_text(
        json.dumps({"last_issue": 999, "halt": True, "halt_reason": "stopped"}),
        encoding="utf-8",
    )
    store = _store(tmp_path)
    seed = tmp_path / "seed.jsonl"
    seed.write_text("", encoding="utf-8")
    code = qmod.main(
        [
            "--queue-path",
            str(store.queue_path),
            "--state-path",
            str(store.state_path),
            "--lock-path",
            str(store.lock_path),
            "migrate",
            "--from-night-queue-state",
            str(night),
            "--seed",
            str(seed),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert "last_issue" not in out
    snap = store.snapshot()
    assert snap.halt is True
    assert snap.halt_reason == "stopped"


def test_no_last_issue_string_in_src():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "issuesmith"
    hits = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "last_issue" in text:
            hits.append(str(path.relative_to(root)))
    assert hits == []


def test_milestone_and_language_surface_removed():
    import issuesmith.milestone as milestone_mod

    assert not hasattr(milestone_mod, "milestone_last_issue_terminal_ok")
    assert "queue.skipped" not in language.EN.messages
