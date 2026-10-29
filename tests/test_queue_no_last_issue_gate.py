"""Regression: queue dispatch no longer gates on legacy last_issue (#5763)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import get_args
from zoneinfo import ZoneInfo

import pytest

from issuesmith import queue as qmod
from issuesmith.queue import PlanReason, plan
from issuesmith.queue_store import QueueStore
from tests.test_queue import _VALID_BODY, _ProdShapeClient, _store

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")


def _write_legacy_last_issue(tmp_path, issue):
    """Leave a serial-era ``last_issue`` key in state.json (the store no longer writes it)."""
    path = tmp_path / "state.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "revision": 0}
    state["last_issue"] = issue
    path.write_text(json.dumps(state), encoding="utf-8")


def _patch_dispatch_gates(monkeypatch):
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])


def _enqueue_draft(store: QueueStore, issue: int) -> None:
    store.enqueue(
        issue=issue,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )


@pytest.mark.parametrize(
    "last_issue,last_state,last_labels",
    [
        (40, "OPEN", [{"name": "issuesmith:develop-running"}]),
        (2820, "CLOSED", [{"name": "scope:milestone"}]),
        (99999, "OPEN", []),
    ],
)
def test_plan_dispatches_despite_legacy_last_issue(
    tmp_path, monkeypatch, last_issue, last_state, last_labels
):
    store = _store(tmp_path)
    _write_legacy_last_issue(tmp_path, last_issue)
    _enqueue_draft(store, 50)
    issues = {
        last_issue: {
            "state": last_state,
            "title": "prev",
            "body": _VALID_BODY,
            "labels": last_labels,
        },
        50: {
            "state": "OPEN",
            "title": "next",
            "body": _VALID_BODY,
            "labels": [],
        },
    }
    client = _ProdShapeClient(
        issues=issues,
        open_issue_rows=[
            {
                "number": 50,
                "title": "next",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
            }
        ],
    )
    _patch_dispatch_gates(monkeypatch)
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    queue_plan = plan(store, client, now=now)
    assert queue_plan.entries
    assert queue_plan.entries[0].decision == "dispatch"
    assert queue_plan.entries[0].reason == "ok"


def test_dispatch_one_ignores_last_issue_fetch_errors(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _write_legacy_last_issue(tmp_path, 40)
    _enqueue_draft(store, 50)

    class Client(_ProdShapeClient):
        def issue_get(self, number, fields=None):
            if number == 40:
                raise RuntimeError("boom")
            return super().issue_get(number, fields=fields)

    client = Client(
        issues={
            50: {
                "state": "OPEN",
                "title": "next",
                "body": _VALID_BODY,
                "labels": [],
            }
        },
        open_issue_rows=[
            {
                "number": 50,
                "title": "next",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
            }
        ],
    )
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    result = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 50


def test_plan_reason_excludes_last_issue_literals():
    literals = set(get_args(PlanReason))
    assert "last_issue_not_terminal" not in literals
    assert "last_issue_fetch_failed" not in literals


def test_dispatch_does_not_update_last_issue(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _write_legacy_last_issue(tmp_path, 40)
    _enqueue_draft(store, 50)
    client = _ProdShapeClient(
        issues={
            50: {
                "state": "OPEN",
                "title": "next",
                "body": _VALID_BODY,
                "labels": [],
            }
        },
        open_issue_rows=[
            {
                "number": 50,
                "title": "next",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
            }
        ],
    )
    _patch_dispatch_gates(monkeypatch)
    now = datetime(2026, 9, 3, 12, 0, tzinfo=_JST)
    result = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert not hasattr(store.snapshot(), "last_issue")


def test_status_omits_last_issue_and_does_not_fetch_it(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path)
    _write_legacy_last_issue(tmp_path, 40)
    _enqueue_draft(store, 50)

    class Client:
        def issue_get(self, number, fields=None):
            if number == 40:
                raise AssertionError("status must not fetch last_issue")
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }

        def list_open_issues_for_queue(self):
            return []

    monkeypatch.setattr(qmod, "get_forge", lambda **kwargs: Client())
    code = qmod.main(
        [
            "--queue-path",
            str(tmp_path / "queue.jsonl"),
            "--state-path",
            str(tmp_path / "state.json"),
            "--lock-path",
            str(tmp_path / "lock"),
            "status",
            "--json",
        ]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "last_issue" not in payload


def test_removed_cli_subcommands_fail(tmp_path):
    with pytest.raises(SystemExit) as skip_exc:
        qmod.main(
            [
                "--queue-path",
                str(tmp_path / "queue.jsonl"),
                "--state-path",
                str(tmp_path / "state.json"),
                "--lock-path",
                str(tmp_path / "lock"),
                "skip",
                "--issue",
                "1",
            ]
        )
    assert skip_exc.value.code == 2

    with pytest.raises(SystemExit) as reset_exc:
        qmod.main(
            [
                "--queue-path",
                str(tmp_path / "queue.jsonl"),
                "--state-path",
                str(tmp_path / "state.json"),
                "--lock-path",
                str(tmp_path / "lock"),
                "reset",
                "--keep-last-issue",
            ]
        )
    assert reset_exc.value.code == 2
