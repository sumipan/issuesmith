"""Queue CP2 dispatch: merge PR detection, relative order, develop dispatch and terminal predecessors."""

from __future__ import annotations

from datetime import datetime

import pytest

from tests import test_queue as _base
from tests.test_queue import _NOW, _VALID_BODY, _ProdShapeClient, _store

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")


class TestCp2MergePrDetection:
    def test_phase_preconditions_merge_already_merged(self):
        """No open PR, already-merged PR present, merge-done missing → already_merged (#2825)."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            closed_prs=[
                {
                    "number": 2900,
                    "title": "c5B9F_c88C5: Issue #2825",
                    "body": "Closes #2825",
                    "state": "CLOSED",
                    "headRefName": "feat/issue-2825",
                    "mergedAt": "2026-09-04T12:00:00Z",
                }
            ]
        )
        ok, why = qmod.phase_preconditions(
            "merge",
            {"state": "OPEN", "labels": [{"name": "issuesmith:develop-done"}]},
            client,
            2825,
        )
        assert ok is True
        assert why == "already_merged"

    def test_dispatch_merge_when_open_pr_closes_in_body(self, tmp_path, monkeypatch):
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=2773, phase="merge", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                2773: {
                    "state": "OPEN",
                    "title": "queue serialize",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:develop-done"}],
                }
            },
            open_prs=[
                {
                    "number": 2789,
                    # ASCII fixture data.
                    "title": "c5B9F_c88C5: Issue #2773",
                    "body": "P1/P2 result c3088_c308A_c81EA_c52D5_c751F_c6210_c3002\n\nCloses #2773",
                    "state": "OPEN",
                    "headRefName": "feat/issue-2773",
                }
            ],
            open_issue_rows=[
                {
                    "number": 2773,
                    "title": "queue serialize",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:develop-done"}],
                }
            ],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:merge-ready"
        assert any(u[0] == 2773 and "issuesmith:merge-ready" in (u[1] or []) for u in client.updates)


class TestCp2NoTitleSupersede:
    def test_newer_same_shape_title_does_not_close(
        self, tmp_path, monkeypatch
    ):
        """Refs #7329."""
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        enqueued = store.enqueue(
            issue=10, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                10: {
                    "state": "OPEN",
                    "title": "app: bump dep to v0.1.0",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[
                {
                    "number": 10,
                    "title": "app: bump dep to v0.1.0",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                },
                {
                    "number": 11,
                    "title": "app: bump dep to v0.2.0",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                },
            ],
        )
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: False)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
        )
        assert result.dispatched is False
        assert 10 not in client.closes
        snap = store.snapshot()
        assert enqueued.request_id not in snap.completed_request_ids


class TestCp2DevelopDispatch:
    def test_dispatch_blocked_when_pipeline_not_idle(self, tmp_path, monkeypatch):
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: False)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
        )
        assert result.dispatched is False
        assert "idle" in result.reason
        assert store.snapshot().active_order
