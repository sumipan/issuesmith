"""Tests for metrics_events contract (#4422)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from issuesmith.andon import Andon, raise_andon
from issuesmith.config import get_config
from issuesmith.metrics_events import append_event, record_step_started
from issuesmith.repair import record_metrics


def _fake_client():
    from unittest.mock import MagicMock

    client = MagicMock()
    client.issue_comment.return_value = {"id": 1, "body": ""}
    client.issue_update.return_value = None
    return client


class TestAppendEvent:
    def test_adds_ts_and_parent_uuid_when_env_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        path = tmp_path / "metrics.jsonl"
        append_event(path, {"event": "requires_check", "step": "p1", "issue": 42})
        line = json.loads(path.read_text().strip())
        assert line["event"] == "requires_check"
        assert line["parent_uuid"] == "u1"
        datetime.fromisoformat(line["ts"])

    def test_omits_parent_uuid_when_env_empty(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
        path = tmp_path / "metrics.jsonl"
        append_event(path, {"event": "requires_check", "step": "p1", "issue": 42})
        line = json.loads(path.read_text().strip())
        assert "parent_uuid" not in line
        assert "ts" in line

    def test_rule_ids_omitted_when_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
        path = tmp_path / "metrics.jsonl"
        record_metrics(path, "requires_repair", "p1", 42)
        line = json.loads(path.read_text().strip())
        assert "rule_ids" not in line

    def test_rule_ids_included_when_provided(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
        path = tmp_path / "metrics.jsonl"
        record_metrics(path, "requires_repair", "p1", 42, rule_ids=["lint.e501", "tests.fail"])
        line = json.loads(path.read_text().strip())
        assert line["rule_ids"] == ["lint.e501", "tests.fail"]


class TestRecordStepStarted:
    def _write_exec(self, path: Path, rows: list[dict]) -> None:
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_writes_step_started_from_exec_row(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        exec_path = tmp_path / "exec.jsonl"
        metrics_path = tmp_path / "metrics.jsonl"
        self._write_exec(
            exec_path,
            [
                {
                    "uuid": "u1",
                    "annotations": {"step_name": "p1"},
                    "idempotency_key": "issuesmith:impl:4401",
                }
            ],
        )
        import dataclasses


        paths = dataclasses.replace(
            get_config().paths,
            exec_jsonl=exec_path,
            metrics=metrics_path,
        )
        record_step_started(paths)
        line = json.loads(metrics_path.read_text().strip())
        assert line == {
            "event": "step_started",
            "parent_uuid": "u1",
            "issue": 4401,
            "step": "p1",
            "workflow": "impl",
            "ts": line["ts"],
        }
        datetime.fromisoformat(line["ts"])

    def test_writes_nothing_when_uuid_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        exec_path = tmp_path / "exec.jsonl"
        metrics_path = tmp_path / "metrics.jsonl"
        self._write_exec(exec_path, [{"uuid": "other", "annotations": {"step_name": "p1"}, "idempotency_key": "issuesmith:impl:4401"}])
        import dataclasses

        paths = dataclasses.replace(get_config().paths, exec_jsonl=exec_path, metrics=metrics_path)
        record_step_started(paths)
        assert not metrics_path.exists()

    def test_writes_nothing_when_step_name_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        exec_path = tmp_path / "exec.jsonl"
        metrics_path = tmp_path / "metrics.jsonl"
        self._write_exec(exec_path, [{"uuid": "u1", "annotations": {}, "idempotency_key": "issuesmith:impl:4401"}])
        import dataclasses

        paths = dataclasses.replace(get_config().paths, exec_jsonl=exec_path, metrics=metrics_path)
        record_step_started(paths)
        assert not metrics_path.exists()

    def test_writes_nothing_when_issue_not_numeric(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        exec_path = tmp_path / "exec.jsonl"
        metrics_path = tmp_path / "metrics.jsonl"
        self._write_exec(
            exec_path,
            [{"uuid": "u1", "annotations": {"step_name": "p1"}, "idempotency_key": "issuesmith:impl:abc"}],
        )
        import dataclasses

        paths = dataclasses.replace(get_config().paths, exec_jsonl=exec_path, metrics=metrics_path)
        record_step_started(paths)
        assert not metrics_path.exists()

    def test_swallows_read_errors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        metrics_path = tmp_path / "metrics.jsonl"
        import dataclasses

        paths = dataclasses.replace(
            get_config().paths,
            exec_jsonl=tmp_path / "missing.jsonl",
            metrics=metrics_path,
        )
        record_step_started(paths)
        assert not metrics_path.exists()


class TestEngineParentUuid:
    def test_record_task_metrics_adds_parent_uuid(self, monkeypatch):
        monkeypatch.setenv("GHDAG_TASK_UUID", "u1")
        monkeypatch.setenv("METRICS_JSONL_PATH", "/tmp/test-metrics.jsonl")
        recorded: list = []

        class FakeRecorder:
            def record(self, metrics):
                recorded.append(metrics)

        monkeypatch.setattr("issuesmith.engine._metrics_recorder", lambda: FakeRecorder())
        from issuesmith.engine import _record_task_metrics

        _record_task_metrics(
            role="design",
            engine="claude",
            model="claude-sonnet-4-6",
            template="p1.md",
            status="success",
            started_at=0.0,
            finished_at=1.0,
            usage=None,
        )
        assert recorded[0].additional_tags["parent_uuid"] == "u1"

    def test_record_task_metrics_omits_parent_uuid_when_env_empty(self, monkeypatch):
        monkeypatch.delenv("GHDAG_TASK_UUID", raising=False)
        monkeypatch.setenv("METRICS_JSONL_PATH", "/tmp/test-metrics.jsonl")
        recorded: list = []

        class FakeRecorder:
            def record(self, metrics):
                recorded.append(metrics)

        monkeypatch.setattr("issuesmith.engine._metrics_recorder", lambda: FakeRecorder())
        from issuesmith.engine import _record_task_metrics

        _record_task_metrics(
            role="design",
            engine="claude",
            model="claude-sonnet-4-6",
            template="p1.md",
            status="success",
            started_at=0.0,
            finished_at=1.0,
            usage=None,
        )
        assert "parent_uuid" not in recorded[0].additional_tags


class TestAndonMetrics:
    def test_raise_andon_includes_issue_step_kind_ts(self, tmp_path):
        client = _fake_client()
        andon = Andon(
            id="impl:4401:p1:0",
            kind="decision",
            issue=4401,
            step="p1",
            summary="test",
        )
        mfile = tmp_path / "metrics.jsonl"
        raise_andon(client, andon, metrics_path=mfile)
        line = json.loads(mfile.read_text().strip())
        assert line["event"] == "andon_raised"
        assert line["andon_id"] == andon.id
        assert line["issue"] == 4401
        assert line["step"] == "p1"
        assert line["kind"] == "decision"
        assert "ts" in line


class TestConftestGuards:
    def test_metrics_path_under_tmp(self, tmp_path_factory):
        basetemp = tmp_path_factory.getbasetemp().resolve()
        metrics = get_config().paths.metrics.resolve()
        assert metrics.is_relative_to(basetemp)

    def test_path_open_write_outside_tmp_raises(self):
        with pytest.raises(AssertionError, match="side effect outside tmp"):
            Path("outside.jsonl").open("a")

class TestDispatchRequiresRepairRuleIds:
    def test_requires_repair_passes_blocking_rule_ids(self, tmp_path, monkeypatch):
        from dataclasses import dataclass

        from issuesmith.ops import dispatch as dispatch_mod

        recorded: list[tuple] = []

        def capture(event, step_id, issue_num, rule_ids=None):
            recorded.append((event, step_id, issue_num, rule_ids))

        monkeypatch.setattr(dispatch_mod, "_safe_record_metrics", capture)

        @dataclass
        class Violation:
            rule_id: str
            message: str = "msg"
            auto_fixable: bool = False

        blocking = [Violation("lint.e501"), Violation("tests.fail"), Violation("lint.e501")]
        # Simulate the call site in run_requires_loop
        rule_ids = list(dict.fromkeys(v.rule_id for v in blocking))
        dispatch_mod._safe_record_metrics("requires_repair", "p1", 42, rule_ids=rule_ids)
        assert recorded == [("requires_repair", "p1", 42, ["lint.e501", "tests.fail"])]
