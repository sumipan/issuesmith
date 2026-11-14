"""Queue LLM triage, dispatch repair and base dispatch."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tests import test_queue as _base
from tests.test_queue import _NOW, _VALID_BODY, _ProdShapeClient, _store

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")


_EMPTY_ALLOW_PATHS_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths: []\n"
    "```\n"
)


class TestQueueRepair:
    @staticmethod
    def _tracking_client(client: _ProdShapeClient) -> _ProdShapeClient:
        original_update = client.issue_update

        def tracking_update(number, labels_add=None, labels_remove=None):
            original_update(number, labels_add=labels_add, labels_remove=labels_remove)
            labels = list(client.issues[number].get("labels") or [])
            remove = set(labels_remove or [])
            labels = [lab for lab in labels if lab.get("name") not in remove]
            for lab in labels_add or []:
                if not any(existing.get("name") == lab for existing in labels):
                    labels.append({"name": lab})
            client.issues[number]["labels"] = labels

        client.issue_update = tracking_update  # type: ignore[method-assign]
        return client

    def test_dispatch_repair_enqueues_draft_and_keeps_request_active(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod
        from issuesmith.queue_triage import comment_marker

        store = _store(tmp_path)
        r = store.enqueue(
            issue=50,
            phase="develop",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        client = self._tracking_client(
            _ProdShapeClient(
                issues={
                    50: {
                        "state": "OPEN",
                        "title": "t",
                        "body": _EMPTY_ALLOW_PATHS_BODY,
                        "labels": [{"name": "issuesmith:draft-done"}],
                    },
                },
                open_issue_rows=[
                    {
                        "number": 50,
                        "title": "t",
                        "state": "open",
                        "body": _EMPTY_ALLOW_PATHS_BODY,
                        "labels": [{"name": "issuesmith:draft-done"}],
                    }
                ],
            )
        )
        monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: True)
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
        assert result.label == "issuesmith:draft-ready"
        snap = store.snapshot()
        assert r.request_id in snap.active_order
        assert (snap.request_meta.get(r.request_id) or {}).get("repair_count") == 1
        rejected_updates = [
            u for u in client.updates if u[1] and "issuesmith:rejected" in u[1]
        ]
        assert rejected_updates == []
        draft_removes = [
            u for u in client.updates
            if u[2] and "issuesmith:draft-done" in u[2]
        ]
        assert draft_removes
        draft_requests = [
            req for req in snap.requests.values()
            if req.issue == 50 and req.phase == "draft" and req.source == "queue-repair"
        ]
        assert len(draft_requests) == 1
        repair_comments = [
            body for _, body in client.comments
            if comment_marker(r.request_id, "repair") in body
        ]
        assert len(repair_comments) == 1

    def test_dispatch_repair_limit_rejects(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        r = store.enqueue(
            issue=50,
            phase="develop",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        store.update_meta(r.request_id, {"repair_count": 1})
        client = self._tracking_client(
            _ProdShapeClient(
                issues={
                    50: {
                        "state": "OPEN",
                        "title": "t",
                        "body": _EMPTY_ALLOW_PATHS_BODY,
                        "labels": [{"name": "issuesmith:draft-done"}],
                    },
                },
                open_issue_rows=[
                    {
                        "number": 50,
                        "title": "t",
                        "state": "open",
                        "body": _EMPTY_ALLOW_PATHS_BODY,
                        "labels": [{"name": "issuesmith:draft-done"}],
                    }
                ],
            )
        )
        monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        rejected_updates = [
            u for u in client.updates if u[1] and "issuesmith:rejected" in u[1]
        ]
        assert rejected_updates
        assert rejected_updates[0][0] == 50
        assert r.request_id not in store.snapshot().active_order

    def test_dispatch_repair_comment_is_idempotent(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod
        from issuesmith.queue_triage import comment_marker

        store = _store(tmp_path)
        r = store.enqueue(
            issue=50,
            phase="develop",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        client = self._tracking_client(
            _ProdShapeClient(
                issues={
                    50: {
                        "state": "OPEN",
                        "title": "t",
                        "body": _EMPTY_ALLOW_PATHS_BODY,
                        "labels": [{"name": "issuesmith:draft-done"}],
                    },
                },
                open_issue_rows=[
                    {
                        "number": 50,
                        "title": "t",
                        "state": "open",
                        "body": _EMPTY_ALLOW_PATHS_BODY,
                        "labels": [{"name": "issuesmith:draft-done"}],
                    }
                ],
            )
        )
        marker = comment_marker(r.request_id, "repair")

        def get_comments(number):
            return [{"body": body} for _, body in client.comments if marker in body]

        client.get_issue_comments = get_comments  # type: ignore[method-assign]
        monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        for _ in range(2):
            qmod.dispatch_one(
                now=now,
                client=client,
                store=store,
                skip_seed=True,
                call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
            )
        repair_comments = [
            body for _, body in client.comments
            if marker in body
        ]
        assert len(repair_comments) == 1


class TestTriage:
    def test_llm_adopt_and_constraints(self, tmp_path):
        from datetime import timedelta

        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        r_low = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        r_high = store.enqueue(
            issue=2, phase="draft", source="s", actor_kind="automation",
            priority="high", requested_by=["a"], requested_at=_NOW,
        )
        r_aged = store.enqueue(
            issue=3, phase="draft", source="s", actor_kind="automation",
            priority="normal", requested_by=["a"], requested_at=old,
        )
        snap = store.snapshot()

        def fake_llm(prompt, **kwargs):
            order = [r_low.request_id, r_aged.request_id, r_high.request_id]
            payload = {
                "order": order,
                "decisions": [
                    {"request_id": rid, "decision": "keep", "reason": "ok", "uncertain_flag": False}
                    for rid in order
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={
                1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"},
                2: {"title": "b", "body": _VALID_BODY, "labels": [], "state": "OPEN"},
                3: {"title": "c", "body": _VALID_BODY, "labels": [], "state": "OPEN"},
            },
            store=store,
            call_llm=fake_llm,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is True
        assert result.order[0] == r_high.request_id
        assert r_aged.request_id in result.order
        # aged normal before low
        assert result.order.index(r_aged.request_id) < result.order.index(r_low.request_id)
        log = (tmp_path / "triage.jsonl").read_text(encoding="utf-8")
        assert "prompt_version" in log
        assert "trace_id" in log

    def test_human_reject_converted_to_keep(self, tmp_path):
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        snap = store.snapshot()

        def fake_llm(prompt, **kwargs):
            payload = {
                "order": [r.request_id],
                "decisions": [
                    {
                        "request_id": r.request_id,
                        "decision": "reject",
                        "reason": "noise",
                        "uncertain_flag": False,
                    }
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=fake_llm,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.decisions[0].decision == "keep"

    def test_llm_failure_falls_back(self, tmp_path):
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()

        def boom(prompt, **kwargs):
            raise TimeoutError("timeout")

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=boom,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is False
        assert result.order == [r.request_id]
        assert "timeout" in (result.fallback_reason or "")
        assert (tmp_path / "triage.jsonl").exists()

    def test_default_engine_model_is_allowlisted(self):
        """Default triage engine/model must exist in the configs allowlist (#2803)."""
        from ghdag.llm import EngineModelError, validate_engine_model

        validate_engine_model("claude", "claude-sonnet-4-6")
        with pytest.raises(EngineModelError):
            validate_engine_model("cursor", "gemini-3-flash")

    def test_engine_model_mismatch_is_config_error(self, tmp_path):
        """engine/model mismatch is a config error, not llm failed (#2803)."""
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()
        called = {"n": 0}

        def fake_llm(prompt, **kwargs):
            called["n"] += 1
            payload = {
                "order": [r.request_id],
                "decisions": [
                    {
                        "request_id": r.request_id,
                        "decision": "keep",
                        "reason": "ok",
                        "uncertain_flag": False,
                    }
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=fake_llm,
            engine="cursor",
            model="gemini-3-flash",
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is False
        assert called["n"] == 0
        assert (result.fallback_reason or "").startswith("config error:")
        assert "llm failed:" not in (result.fallback_reason or "")
        log = json.loads((tmp_path / "triage.jsonl").read_text(encoding="utf-8").strip())
        assert log["config"]["engine"] == "cursor"
        assert log["config"]["model"] == "gemini-3-flash"
        assert (log.get("fallback_reason") or "").startswith("config error:")

    def test_default_triage_logs_claude_engine_model(self, tmp_path):
        """Default triage log records claude / claude-sonnet-4-6 (#2803)."""
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()

        def fake_llm(prompt, **kwargs):
            assert kwargs.get("route") == [("claude", "claude-sonnet-4-6")]
            payload = {
                "order": [r.request_id],
                "decisions": [
                    {
                        "request_id": r.request_id,
                        "decision": "keep",
                        "reason": "ok",
                        "uncertain_flag": False,
                    }
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=fake_llm,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is True
        log = json.loads((tmp_path / "triage.jsonl").read_text(encoding="utf-8").strip())
        assert log["config"]["engine"] == "claude"
        assert log["config"]["model"] == "claude-sonnet-4-6"


class TestDispatch:
    def test_dispatch_draft_when_gates_pass(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )

        class Client:
            def __init__(self):
                self.updates = []
                self.comments = []

            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }

            def issue_update(self, number, labels_add=None, labels_remove=None):
                self.updates.append((number, labels_add))

            def issue_comment(self, number, body):
                self.comments.append((number, body))

            def get_issue_comments(self, number):
                return []

            def list_issues(self, label, state="open"):
                return []

            def list_open_issues_for_queue(self):
                return []

        client = Client()
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
        assert result.label == "issuesmith:draft-ready"
        assert client.updates == [(50, ["issuesmith:draft-ready"])]
        snap = store.snapshot()
        assert snap.active_order == []

    def test_dispatch_blocked_when_running_elsewhere(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )

        class Client:
            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }

            def list_issues(self, label, state="open"):
                # Only draft-running: develop-running would trigger #3092
                # in_flight recovery and allow_paths-conflict with #50.
                if label == "issuesmith:draft-running":
                    return [{"number": 99, "labels": [{"name": label}]}]
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

            def issue_update(self, number, labels_add=None, labels_remove=None):
                pass

            def issue_comment(self, number, body):
                pass

        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=Client(),
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.issue == 50

    def test_comment_marker_idempotent(self, tmp_path):
        from issuesmith import queue as qmod
        from issuesmith.queue_triage import comment_marker

        store = _store(tmp_path)
        r = store.enqueue(
            issue=7, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["bot"], requested_at=_NOW,
        )
        marker = comment_marker(r.request_id, "rejected")

        class Client:
            def __init__(self):
                self.comments = []

            def get_issue_comments(self, number):
                return [{"body": f"already\n{marker}"}]

            def issue_comment(self, number, body):
                self.comments.append(body)

            def issue_update(self, *a, **k):
                pass

            def issue_close(self, *a):
                pass

        client = Client()
        qmod._ensure_comment(client, 7, r.request_id, "rejected", "reason")
        assert client.comments == []
        qmod._apply_terminal(store, client, r.request_id, 7, "rejected", "reason", add_rejected_label=True)
        # second apply is no-op for comment
        qmod._apply_terminal(store, client, r.request_id, 7, "rejected", "reason", add_rejected_label=True)
        assert client.comments == []

    def test_dispatch_blocked_outside_source_window(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="release-watcher", actor_kind="automation",
            priority="low", requested_by=["release-watcher"], requested_at=_NOW,
        )

        class Client:
            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }

            def list_issues(self, label, state="open"):
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        # Outside 01:00–07:00 JST window
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=Client(),
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "no dispatchable" in result.reason
        assert store.snapshot().active_order  # request retained

    def test_dispatch_blocked_when_engines_paused(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )

        class Client:
            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }

            def list_issues(self, label, state="open"):
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: ["claude"])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=Client(),
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "paused" in result.reason
        assert "role=" in result.reason

    def test_dispatch_develop_requires_draft_done(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )

        class Client:
            def __init__(self):
                self.updates = []
                self.comments = []

            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],  # no draft-done
                }

            def issue_update(self, number, labels_add=None, labels_remove=None):
                self.updates.append((number, labels_add))

            def issue_comment(self, number, body):
                self.comments.append((number, body))

            def list_issues(self, label, state="open"):
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

        client = Client()
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
        assert result.dispatched is False
        assert client.updates == []
        assert store.snapshot().active_order  # retained
