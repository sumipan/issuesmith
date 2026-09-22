"""tests/observe/test_events.py -- event detection tests (AC-1, AC-2)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from issuesmith.config import get_config
from issuesmith.observe import observe
from issuesmith.observe.events import (
    IssueStallEvent,
    LabelDriftEvent,
    OrphanExecEvent,
    SystemicStepFailureEvent,
)
from issuesmith.queue_store import QueueSnapshot, QueueStore

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _empty_snap(store: QueueStore | None = None, tmp_path: Path | None = None) -> QueueSnapshot:
    if store is not None:
        return store.snapshot()
    assert tmp_path is not None
    return _store(tmp_path).snapshot()


def _snap_with_in_flight(base: QueueSnapshot, in_flight: list[dict]) -> QueueSnapshot:
    import dataclasses
    return dataclasses.replace(base, in_flight=in_flight)


def _fake_client(issues: dict | None = None) -> MagicMock:
    client = MagicMock()
    issues = issues or {}
    client.issue_get.side_effect = lambda n, **kwargs: issues.get(n, {"number": n, "state": "OPEN", "labels": []})
    return client


def _enqueue(store: QueueStore, issue: int, phase: str = "draft") -> None:
    store.enqueue(
        issue=issue,
        phase=phase,
        source="test",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW.isoformat(),
    )


class TestIssueStallDetection:
    def test_stall_event_fires_when_in_flight_exceeds_threshold(self, tmp_path):
        store = _store(tmp_path)
        _enqueue(store, 100, "develop")
        base = store.snapshot()
        dispatched_at = (_NOW - timedelta(minutes=130)).isoformat()
        snap = _snap_with_in_flight(base, [
            {"issue": 100, "engine": "claude", "role": "implementation", "dispatched_at": dispatched_at}
        ])

        cfg = get_config()
        client = _fake_client()
        events = observe(snap, client, cfg, now=_NOW)
        stall_events = [e for e in events if isinstance(e, IssueStallEvent)]
        assert len(stall_events) == 1
        assert stall_events[0].issue == 100
        assert stall_events[0].minutes >= 130
        client.issue_update.assert_not_called()

    def test_no_stall_when_recently_dispatched(self, tmp_path):
        store = _store(tmp_path)
        _enqueue(store, 200, "draft")
        base = store.snapshot()
        dispatched_at = (_NOW - timedelta(minutes=30)).isoformat()
        snap = _snap_with_in_flight(base, [
            {"issue": 200, "engine": "claude", "role": "design", "dispatched_at": dispatched_at}
        ])

        cfg = get_config()
        client = _fake_client()
        events = observe(snap, client, cfg, now=_NOW)
        stall_events = [e for e in events if isinstance(e, IssueStallEvent)]
        assert len(stall_events) == 0


class TestOrphanExecDetection:
    def test_orphan_exec_fires_when_no_in_flight(self, tmp_path):
        exec_path = tmp_path / "exec.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        uuid = "aaaabbbb-0000-0000-0000-000000000001"
        exec_path.write_text(
            json.dumps({
                "uuid": uuid,
                "idempotency_key": "issuesmith:develop:100",
            }) + "\n",
            encoding="utf-8",
        )

        snap = _empty_snap(tmp_path=tmp_path)
        cfg = get_config()
        client = _fake_client()

        import dataclasses
        patched_paths = dataclasses.replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir)
        patched_cfg = dataclasses.replace(cfg, paths=patched_paths)

        events = observe(snap, client, patched_cfg, now=_NOW)
        orphan_events = [e for e in events if isinstance(e, OrphanExecEvent)]
        assert len(orphan_events) == 1
        assert orphan_events[0].uuid == uuid
        assert orphan_events[0].issue == 100

    def test_no_orphan_when_in_flight_matches(self, tmp_path):
        exec_path = tmp_path / "exec.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        uuid = "aaaabbbb-0000-0000-0000-000000000002"
        exec_path.write_text(
            json.dumps({
                "uuid": uuid,
                "idempotency_key": "issuesmith:develop:200",
            }) + "\n",
            encoding="utf-8",
        )

        base = _empty_snap(tmp_path=tmp_path)
        snap = _snap_with_in_flight(base, [
            {"issue": 200, "engine": "claude", "dispatched_at": _NOW.isoformat()}
        ])

        cfg = get_config()
        client = _fake_client()

        import dataclasses
        patched_paths = dataclasses.replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir)
        patched_cfg = dataclasses.replace(cfg, paths=patched_paths)

        events = observe(snap, client, patched_cfg, now=_NOW)
        orphan_events = [e for e in events if isinstance(e, OrphanExecEvent)]
        assert len(orphan_events) == 0

    def test_no_orphan_when_done_marker_exists(self, tmp_path):
        exec_path = tmp_path / "exec.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        uuid = "aaaabbbb-0000-0000-0000-000000000003"
        (done_dir / uuid).touch()
        exec_path.write_text(
            json.dumps({
                "uuid": uuid,
                "idempotency_key": "issuesmith:develop:300",
            }) + "\n",
            encoding="utf-8",
        )

        snap = _empty_snap(tmp_path=tmp_path)
        cfg = get_config()
        client = _fake_client()

        import dataclasses
        patched_paths = dataclasses.replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir)
        patched_cfg = dataclasses.replace(cfg, paths=patched_paths)

        events = observe(snap, client, patched_cfg, now=_NOW)
        orphan_events = [e for e in events if isinstance(e, OrphanExecEvent)]
        assert len(orphan_events) == 0


class TestLabelDriftDetection:
    def test_label_drift_fires_when_managed_label_missing(self, tmp_path):
        store = _store(tmp_path)
        _enqueue(store, 300, "draft")
        snap = store.snapshot()

        cfg = get_config()
        client = _fake_client({
            300: {"number": 300, "state": "OPEN", "labels": []},
        })

        events = observe(snap, client, cfg, now=_NOW)
        drift_events = [e for e in events if isinstance(e, LabelDriftEvent)]
        assert any(e.issue == 300 for e in drift_events)
        client.issue_update.assert_not_called()

    def test_no_label_drift_when_labels_match(self, tmp_path):
        store = _store(tmp_path)
        _enqueue(store, 400, "draft")
        snap = store.snapshot()

        cfg = get_config()
        ns = cfg.label_namespace
        client = _fake_client({
            400: {"number": 400, "state": "OPEN", "labels": [{"name": f"{ns}:queued"}]},
        })

        events = observe(snap, client, cfg, now=_NOW)
        drift_events = [e for e in events if isinstance(e, LabelDriftEvent) and e.issue == 400]
        assert len(drift_events) == 0


class TestSystemicStepFailureDetection:
    def test_systemic_failure_fires_with_two_issues_no_template(self, tmp_path):
        metrics_path = tmp_path / "metrics.jsonl"
        now_ts = _NOW.timestamp()
        records = [
            {
                "uuid": "uuid-001",
                "status": "failed",
                "finished_at": now_ts - 300,
                "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "", "issue": "100"},
            },
            {
                "uuid": "uuid-002",
                "status": "failed",
                "finished_at": now_ts - 200,
                "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "", "issue": "101"},
            },
        ]
        metrics_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        snap = _empty_snap(tmp_path=tmp_path)
        cfg = get_config()
        client = _fake_client()
        events = observe(snap, client, cfg, metrics_path=metrics_path, now=_NOW)
        systemic = [e for e in events if isinstance(e, SystemicStepFailureEvent)]
        assert len(systemic) == 1
        assert systemic[0].step == "cp2"
        assert systemic[0].failure_class == "ValueError"
        assert 100 in systemic[0].issues
        assert 101 in systemic[0].issues

    def test_systemic_failure_not_fired_with_only_one_issue(self, tmp_path):
        metrics_path = tmp_path / "metrics.jsonl"
        now_ts = _NOW.timestamp()
        record = {
            "uuid": "uuid-001",
            "status": "failed",
            "finished_at": now_ts - 60,
            "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "", "issue": "100"},
        }
        metrics_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

        snap = _empty_snap(tmp_path=tmp_path)
        cfg = get_config()
        client = _fake_client()
        events = observe(snap, client, cfg, metrics_path=metrics_path, now=_NOW)
        assert not any(isinstance(e, SystemicStepFailureEvent) for e in events)

    def test_systemic_failure_not_fired_when_template_present(self, tmp_path):
        metrics_path = tmp_path / "metrics.jsonl"
        now_ts = _NOW.timestamp()
        records = [
            {
                "uuid": "uuid-001",
                "status": "failed",
                "finished_at": now_ts - 60,
                "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "cp2.md", "issue": "100"},
            },
            {
                "uuid": "uuid-002",
                "status": "failed",
                "finished_at": now_ts - 30,
                "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "", "issue": "101"},
            },
        ]
        metrics_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        snap = _empty_snap(tmp_path=tmp_path)
        cfg = get_config()
        client = _fake_client()
        events = observe(snap, client, cfg, metrics_path=metrics_path, now=_NOW)
        assert not any(isinstance(e, SystemicStepFailureEvent) for e in events)

    def test_systemic_failure_ignores_records_outside_window(self, tmp_path):
        metrics_path = tmp_path / "metrics.jsonl"
        now_ts = _NOW.timestamp()
        old_ts = now_ts - 7200
        records = [
            {
                "uuid": "uuid-001",
                "status": "failed",
                "finished_at": old_ts,
                "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "", "issue": "100"},
            },
            {
                "uuid": "uuid-002",
                "status": "failed",
                "finished_at": old_ts,
                "additional_tags": {"step": "cp2", "failure_class": "ValueError", "template": "", "issue": "101"},
            },
        ]
        metrics_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        snap = _empty_snap(tmp_path=tmp_path)
        cfg = get_config()
        client = _fake_client()
        events = observe(snap, client, cfg, metrics_path=metrics_path, now=_NOW)
        assert not any(isinstance(e, SystemicStepFailureEvent) for e in events)
