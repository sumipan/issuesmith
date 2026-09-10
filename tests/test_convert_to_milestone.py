"""tests/test_convert_to_milestone.py — convert-to-milestone CLI。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from issuesmith import convert_to_milestone as ctm


class FakeClient:
    def __init__(
        self,
        *,
        labels: list[str] | None = None,
        milestones: list[dict[str, Any]] | None = None,
        issue_milestone: dict[str, Any] | None = None,
    ) -> None:
        self.labels = set(labels or [])
        self.milestones = list(milestones or [])
        self.issue_milestone = issue_milestone
        self.created_milestones: list[str] = []
        self.updates: list[dict[str, Any]] = []
        self._next_ms = 100

    def issue_get(self, number: int, fields=None) -> dict[str, Any]:
        return {
            "number": number,
            "labels": [{"name": n} for n in sorted(self.labels)],
            "milestone": self.issue_milestone,
        }

    def issue_update(self, number: int, **kwargs) -> None:
        self.updates.append({"number": number, **kwargs})
        for lab in kwargs.get("labels_add") or []:
            self.labels.add(lab)
        for lab in kwargs.get("labels_remove") or []:
            self.labels.discard(lab)
        if "milestone" in kwargs and kwargs["milestone"] is not None:
            self.issue_milestone = {"number": kwargs["milestone"], "title": "attached"}

    def milestone_list(self) -> list[dict[str, Any]]:
        return list(self.milestones)

    def milestone_create(self, title: str, description: str = "") -> int:
        self.created_milestones.append(title)
        num = self._next_ms
        self._next_ms += 1
        self.milestones.append({"number": num, "title": title})
        return num

    def api_request(self, path: str, **kwargs):
        if "milestones" in path:
            return self.milestone_list()
        return {}


@pytest.fixture
def repo_layout(tmp_path: Path, monkeypatch) -> Path:
    jobs = tmp_path / "jobs"
    (jobs / "running").mkdir(parents=True)
    (jobs / "cancel").mkdir(parents=True)
    (jobs / "done").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)
    exec_path = jobs / "exec.jsonl"
    exec_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(ctm, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(ctm, "_jobs_dir", lambda: jobs)
    monkeypatch.setattr(ctm, "_exec_path", lambda: exec_path)
    return tmp_path


def _write_running(jobs: Path, uuid: str, payload: dict[str, Any]) -> None:
    path = jobs / "running" / f"{uuid}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cancel_creates_file_for_running_issue(repo_layout: Path, monkeypatch) -> None:
    jobs = repo_layout / "jobs"
    uuid = "run-uuid-3059"
    _write_running(jobs, uuid, {"pid": 1, "pgid": 1, "issue": 3059})
    (jobs / "exec.jsonl").write_text(
        json.dumps({"uuid": uuid, "idempotency_key": "issuesmith:impl:3059"}) + "\n",
        encoding="utf-8",
    )
    client = FakeClient(labels=["issuesmith:develop-running"])
    store = MagicMock()
    redispatch = MagicMock(return_value=0)
    monkeypatch.setattr(ctm, "_github_client", lambda: client)
    monkeypatch.setattr(ctm, "_queue_store", lambda: store)
    monkeypatch.setattr(ctm, "cmd_redispatch", redispatch)
    rc = ctm.convert_to_milestone(3059, dry_run=False)
    assert rc == 0
    assert (jobs / "cancel" / uuid).is_file()


def test_labels_remove_develop_add_milestone(repo_layout: Path, monkeypatch) -> None:
    client = FakeClient(
        labels=["issuesmith:develop-running", "issuesmith:sub-ready", "other"]
    )
    store = MagicMock()
    redispatch = MagicMock(return_value=0)
    monkeypatch.setattr(ctm, "_github_client", lambda: client)
    monkeypatch.setattr(ctm, "_queue_store", lambda: store)
    monkeypatch.setattr(ctm, "cmd_redispatch", redispatch)
    assert ctm.convert_to_milestone(42, dry_run=False) == 0
    assert "issuesmith:develop-running" not in client.labels
    assert "issuesmith:sub-ready" not in client.labels
    assert "scope:milestone" in client.labels
    assert "issuesmith:draft-done" in client.labels
    assert "other" in client.labels


def test_milestone_create_skipped_when_exists(repo_layout: Path, monkeypatch) -> None:
    client = FakeClient(
        labels=["issuesmith:develop-running"],
        milestones=[{"number": 7, "title": "42-20260910"}],
        issue_milestone=None,
    )
    store = MagicMock()
    monkeypatch.setattr(ctm, "_github_client", lambda: client)
    monkeypatch.setattr(ctm, "_queue_store", lambda: store)
    monkeypatch.setattr(ctm, "cmd_redispatch", MagicMock(return_value=0))
    monkeypatch.setattr(ctm, "_today_yyyymmdd", lambda: "20260910")
    assert ctm.convert_to_milestone(42, dry_run=False) == 0
    assert client.created_milestones == []
    assert any(u.get("milestone") == 7 for u in client.updates)


def test_milestone_create_when_missing(repo_layout: Path, monkeypatch) -> None:
    client = FakeClient(labels=["issuesmith:develop-running"], milestones=[])
    store = MagicMock()
    monkeypatch.setattr(ctm, "_github_client", lambda: client)
    monkeypatch.setattr(ctm, "_queue_store", lambda: store)
    monkeypatch.setattr(ctm, "cmd_redispatch", MagicMock(return_value=0))
    monkeypatch.setattr(ctm, "_today_yyyymmdd", lambda: "20260910")
    assert ctm.convert_to_milestone(42, dry_run=False) == 0
    assert client.created_milestones == ["42-20260910"]
    assert client.issue_milestone is not None


def test_idempotent_second_run(repo_layout: Path, monkeypatch) -> None:
    client = FakeClient(labels=["issuesmith:develop-running"])
    store = MagicMock()
    redispatch = MagicMock(return_value=0)
    monkeypatch.setattr(ctm, "_github_client", lambda: client)
    monkeypatch.setattr(ctm, "_queue_store", lambda: store)
    monkeypatch.setattr(ctm, "cmd_redispatch", redispatch)
    monkeypatch.setattr(ctm, "_today_yyyymmdd", lambda: "20260910")
    assert ctm.convert_to_milestone(99, dry_run=False) == 0
    first_creates = list(client.created_milestones)
    assert ctm.convert_to_milestone(99, dry_run=False) == 0
    assert client.created_milestones == first_creates
    assert store.remove_in_flight.call_count == 2
    assert redispatch.call_count == 2


def test_dry_run_makes_no_side_effects(repo_layout: Path, monkeypatch, capsys) -> None:
    jobs = repo_layout / "jobs"
    uuid = "dry-uuid"
    _write_running(jobs, uuid, {"pid": 1, "issue": 7})
    client = FakeClient(labels=["issuesmith:develop-running"])
    store = MagicMock()
    redispatch = MagicMock(return_value=0)
    monkeypatch.setattr(ctm, "_github_client", lambda: client)
    monkeypatch.setattr(ctm, "_queue_store", lambda: store)
    monkeypatch.setattr(ctm, "cmd_redispatch", redispatch)
    assert ctm.convert_to_milestone(7, dry_run=True) == 0
    assert not (jobs / "cancel" / uuid).exists()
    assert client.updates == []
    assert client.created_milestones == []
    store.remove_in_flight.assert_not_called()
    redispatch.assert_not_called()
    out = capsys.readouterr().out
    assert "cancel" in out.lower() or "dry" in out.lower() or "7" in out
