"""Queue CP2 dispatch: merge PR detection, supersede, relative order, develop dispatch and terminal predecessors."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tests import test_queue as _base
from tests.test_queue import _NOW, _VALID_BODY, _ProdShapeClient, _store

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")


class TestCp2MergePrDetection:
    def test_phase_preconditions_merge_finds_closes_in_body(self):
        """Merge preconditions pass for the usual case of Closes #N in the PR body."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            open_prs=[
                {
                    "number": 2789,
                    # ASCII fixture data.
                    "title": "c5B9F_c88C5: Issue #2773",
                    "body": "P1/P2 result c3088_c308A_c81EA_c52D5_c751F_c6210_c3002\n\nCloses #2773",
                    "state": "OPEN",
                    "headRefName": "feat/issue-2773",
                }
            ]
        )
        issue = {
            "number": 2773,
            "state": "OPEN",
            "title": "t",
            "body": _VALID_BODY,
            "labels": [],
        }
        ok, why = qmod.phase_preconditions("merge", issue, client, 2773)
        assert ok is True, why
        # The raw PR list carries body; no per-PR fetch.
        assert client.pr_get_calls == []

    def test_phase_preconditions_merge_finds_refs_in_body(self):
        """publish.py no longer emits Closes (2026-09-06); Refs #N must still be found."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            open_prs=[
                {
                    "number": 2789,
                    "title": "c5B9F_c88C5: Issue #2773",
                    "body": "P1/P2 result c3088_c308A_c81EA_c52D5_c751F_c6210_c3002\n\nRefs #2773",
                    "state": "OPEN",
                    "headRefName": "feat/issue-2773",
                }
            ]
        )
        issue = {
            "number": 2773,
            "state": "OPEN",
            "title": "t",
            "body": _VALID_BODY,
            "labels": [],
        }
        ok, why = qmod.phase_preconditions("merge", issue, client, 2773)
        assert ok is True, why

    def test_phase_preconditions_merge_search_alone_insufficient(self):
        """pr_list(search='Closes #N') does not inspect body, so pr_get is required."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            open_prs=[
                {
                    "number": 1,
                    # ASCII fixture data.
                    "title": "c5B9F_c88C5: Issue #10",
                    "body": "Closes #10",
                    "state": "OPEN",
                    "headRefName": "feat/x",
                }
            ]
        )
        # search-only path would miss this; our helper must still find it
        listed = client.pr_list(state="open", search="Closes #10")
        assert listed == []  # production search does not match body
        ok, why = qmod.phase_preconditions("merge", {"state": "OPEN", "labels": []}, client, 10)
        assert ok is True, why

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

    def test_phase_preconditions_merge_no_open_or_merged(self):
        """Neither open nor merged PR → rejection message (#2825)."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(open_prs=[], closed_prs=[])
        ok, why = qmod.phase_preconditions("merge", {"state": "OPEN", "labels": []}, client, 10)
        assert ok is False
        assert "no open or merged PR" in why

    def test_dispatch_merge_when_open_pr_closes_in_body(self, tmp_path, monkeypatch):
        from datetime import datetime
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
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:merge-ready"
        assert any(u[0] == 2773 and "issuesmith:merge-ready" in (u[1] or []) for u in client.updates)


class TestCp2SupersedeRepoWide:
    def test_supersede_scans_open_issues_outside_queue(self, tmp_path, monkeypatch):
        """A new same-kind SemVer Issue not yet in the queue is still superseded."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=10, phase="draft", source="release-watcher", actor_kind="automation",
            priority="low", requested_by=["release-watcher"], requested_at=_NOW,
        )
        # Newer same-kind issue #11 is NOT in the queue
        client = _ProdShapeClient(
            issues={
                10: {
                    "state": "OPEN",
                    "title": "nexus: bump mltgnt to v0.22.7",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[
                {
                    "number": 10,
                    "title": "nexus: bump mltgnt to v0.22.7",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                },
                {
                    "number": 11,
                    "title": "nexus: bump mltgnt to v0.23.0",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                },
            ],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        # Inside release-watcher window
        now = datetime(2026, 9, 3, 3, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert 10 in client.closes
        assert any("issues?" in p for p in client.api_request_calls)
        snap = store.snapshot()
        assert snap.active_order == []
        assert len(snap.completed_request_ids) == 1


class TestCp2LlmRelativeOrder:
    def test_same_priority_preserves_llm_relative_order(self):
        """Within the same priority/aging bucket, do not override LLM relative order with requested_at."""
        from datetime import timedelta

        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import apply_deterministic_order_constraints

        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        older = (now - timedelta(days=1)).isoformat()
        newer = now.isoformat()
        r_old = QueueRequest(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            1, "draft", "s", "automation", "normal", older, ("a",),
        )
        r_new = QueueRequest(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            2, "draft", "s", "automation", "normal", newer, ("b",),
        )
        llm_order = [r_new.request_id, r_old.request_id]
        result = apply_deterministic_order_constraints(
            llm_order, {r_old.request_id: r_old, r_new.request_id: r_new}, now=now
        )
        assert result == llm_order

    def test_high_and_aged_still_override_llm(self):
        from datetime import timedelta

        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import apply_deterministic_order_constraints

        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        aged_at = (now - timedelta(days=8)).isoformat()
        r_low = QueueRequest(
            "11111111-1111-4111-8111-111111111111",
            1, "draft", "s", "automation", "low", now.isoformat(), ("a",),
        )
        r_high = QueueRequest(
            "22222222-2222-4222-8222-222222222222",
            2, "draft", "s", "automation", "high", now.isoformat(), ("a",),
        )
        r_aged = QueueRequest(
            "33333333-3333-4333-8333-333333333333",
            3, "draft", "s", "automation", "normal", aged_at, ("a",),
        )
        llm_order = [r_low.request_id, r_aged.request_id, r_high.request_id]
        result = apply_deterministic_order_constraints(
            llm_order,
            {
                r_low.request_id: r_low,
                r_high.request_id: r_high,
                r_aged.request_id: r_aged,
            },
            now=now,
        )
        assert result[0] == r_high.request_id
        assert result.index(r_aged.request_id) < result.index(r_low.request_id)


class TestCp2DevelopDispatch:
    def test_draft_done_release_enqueues_queue_chain_develop(
        self, tmp_path, monkeypatch
    ):
        """Design in_flight release on draft-done also enqueues develop (#5746)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        draft = store.enqueue(
            issue=50,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="high",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        store.complete(draft.request_id, "dispatched")
        store.add_in_flight(50, "claude", role="design", phase="draft")
        client = _ProdShapeClient(
            issues={
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        snap = store.snapshot()
        develop = [
            req
            for req in snap.requests.values()
            if req.issue == 50 and req.phase == "develop" and req.source == "queue-chain"
        ]
        assert len(develop) == 1
        assert develop[0].priority == "high"
        assert not any(e.get("role") == "design" for e in snap.in_flight)
        if result.dispatched:
            assert result.label == "issuesmith:develop-ready"

    def test_dispatch_develop_when_draft_done(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            },
            open_issue_rows=[
                {
                    "number": 50,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
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
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:develop-ready"

    def test_dispatch_blocked_when_pipeline_not_idle(self, tmp_path, monkeypatch):
        from datetime import datetime
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
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "idle" in result.reason
        assert store.snapshot().active_order

    def test_dispatch_not_blocked_by_open_previous_issue(self, tmp_path, monkeypatch):
        """Serial last_issue gate removed (#5691): next issue dispatches while prev is OPEN."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        state_path = tmp_path / "state.json"
        state_path.write_text(
            json.dumps({"schema_version": 1, "revision": 0, "last_issue": 40}),
            encoding="utf-8",
        )
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                40: {
                    "state": "OPEN",
                    "title": "prev",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:develop-running"}],
                },
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                },
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.issue == 50

    def test_parallel_lanes_dispatch_with_stale_state_last_issue(self, tmp_path, monkeypatch):
        """Per-engine concurrency: stale state last_issue does not block dispatch (#2980)."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        (tmp_path / "state.json").write_text(
            json.dumps({"schema_version": 1, "revision": 0, "last_issue": 40}),
            encoding="utf-8",
        )
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        body_a = (
            "```yaml\n"
            "target_repo: sumipan/nexus\n"
            "base_branch: main\n"
            "allow_paths:\n"
            '  - "tools/a/**"\n'
            "```\n"
        )
        body_b = (
            "```yaml\n"
            "target_repo: sumipan/nexus\n"
            "base_branch: main\n"
            "allow_paths:\n"
            '  - "tools/b/**"\n'
            "```\n"
        )
        client = _ProdShapeClient(
            issues={
                40: {
                    "state": "OPEN",
                    "title": "prev",
                    "body": body_a,
                    "labels": [{"name": "issuesmith:develop-running"}],
                },
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": body_b,
                    "labels": [],
                },
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_serial_concurrency", lambda: False)
        monkeypatch.setattr(qmod, "_pipeline_idle_enough_v2", lambda *a, **k: False)
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.issue == 50

    def test_pipeline_ready_tolerates_in_flight_issues_own_pending_steps(
        self, tmp_path, monkeypatch
    ):
        """Found after enabling per-engine concurrency in #2978: an in-flight issue's
        own later steps (cp2/m1/m1r/m2, etc.) being incomplete is expected, but
        treating that as "pipeline not idle" also blocks new dispatch to other
        engines (measured: while #2969's cp2/m1/m1r/m2 stayed incomplete in
        in_flight, _dispatch_pipeline_ready always returned False).
        Tolerate incomplete steps for issues already tracked in in_flight;
        only incomplete steps for issues not in in_flight (orphan tasks) stay blocked.
        """
        from issuesmith import queue as qmod

        exec_path = tmp_path / "exec.jsonl"
        done_dir = tmp_path / "done"
        done_dir.mkdir()
        monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
        monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

        # issue 2969 (in_flight) cp2/m1/m2 still lack done markers.
        lines = [
            {"uuid": "u-cp2", "idempotency_key": "issuesmith:impl:2969"},
            {"uuid": "u-m1", "idempotency_key": "issuesmith:impl:2969"},
            {"uuid": "u-m2", "idempotency_key": "issuesmith:impl:2969"},
        ]
        exec_path.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
        )

        store = _store(tmp_path)
        store.add_in_flight(2969, "codex")
        snap = store.snapshot()
        now = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)

        assert qmod._dispatch_pipeline_ready(snap, 5, now) is True

        # Incomplete steps for another issue (9999) not in in_flight → orphan → still blocked.
        with exec_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"uuid": "u-orphan", "idempotency_key": "issuesmith:impl:9999"})
                + "\n"
            )
        assert qmod._dispatch_pipeline_ready(snap, 5, now) is False

    def test_dispatch_label_boundary_rerun_is_idempotent(self, tmp_path, monkeypatch):
        """Stop right after ready, then rerun: no double label apply; ends terminal."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        r = store.enqueue(
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
            open_issue_rows=[
                {
                    "number": 50,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            ],
        )

        original_update = client.issue_update

        def tracking_update(number, labels_add=None, labels_remove=None):
            original_update(number, labels_add=labels_add, labels_remove=labels_remove)
            for lab in labels_add or []:
                client.issues[50]["labels"].append({"name": lab})

        client.issue_update = tracking_update  # type: ignore[method-assign]

        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        first = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert first.dispatched is True
        second = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert second.dispatched is False
        ready_adds = [
            u for u in client.updates
            if u[1] and "issuesmith:draft-ready" in u[1]
        ]
        assert len(ready_adds) == 1
        assert r.request_id in store.snapshot().completed_request_ids


# --- #2825: terminal-without-merge gate / superseded label ---


@pytest.mark.parametrize(
    "terminal_label",
    [
        "issuesmith:sub-ready",
        "issuesmith:sub-done",
        "issuesmith:rejected",
        "issuesmith:superseded",
    ],
)
def test_dispatch_allows_when_previous_issue_terminal_without_merge(
    tmp_path, monkeypatch, terminal_label
):
    """When last_issue is CLOSED + TERMINAL_WITHOUT_MERGE, the next can dispatch (#2825)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.enqueue(
        issue=50, phase="draft", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )
    client = _ProdShapeClient(
        issues={
            40: {
                "state": "CLOSED",
                "title": "prev",
                "body": _VALID_BODY,
                "labels": [{"name": terminal_label}],
            },
            50: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            },
        },
        open_issue_rows=[
            {
                "number": 50,
                "title": "t",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
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
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 50
    assert store.snapshot().halt is False


def test_apply_terminal_superseded_adds_label(tmp_path):
    """On superseded terminal, add the issuesmith:superseded label (#2825)."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=7, phase="draft", source="s", actor_kind="automation",
        priority="low", requested_by=["bot"], requested_at=_NOW,
    )

    class Client:
        def __init__(self):
            self.updates = []
            self.closes = []

        def get_issue_comments(self, number):
            return []

        def issue_comment(self, number, body):
            pass

        def issue_update(self, number, labels_add=None, labels_remove=None):
            self.updates.append((number, labels_add, labels_remove))

        def issue_close(self, number):
            self.closes.append(number)

    client = Client()
    qmod._apply_terminal(
        store, client, r.request_id, 7, "superseded", "superseded by #8",
        close_issue=True,
    )
    assert any(
        u[1] and "issuesmith:superseded" in u[1]
        for u in client.updates
    )
    assert client.closes == [7]
