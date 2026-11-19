"""Queue LLM triage, dispatch repair and base dispatch."""

from __future__ import annotations

import json
from datetime import datetime

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


    def test_dispatch_repair_comment_is_idempotent(self, tmp_path, monkeypatch):
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


class TestDispatch:


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
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        seed_path = tmp_path / "night-queue.yaml"
        seed_path.write_text(
            "window:\n  start: '01:00'\n  end: '07:00'\n  extra_sources:\n    - watcher\n",
            encoding="utf-8",
        )

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="watcher", actor_kind="automation",
            priority="low", requested_by=["watcher"], requested_at=_NOW,
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
            seed_path=seed_path,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "no dispatchable" in result.reason
        assert store.snapshot().active_order  # request retained


def test_extra_source_waits_outside_window(tmp_path, monkeypatch):
    """Refs #7329."""
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod
    from issuesmith.queue_triage import seed_extra_sources, seed_window

    seed_path = tmp_path / "night-queue.yaml"
    seed_path.write_text(
        "window:\n  start: '01:00'\n  end: '07:00'\n  extra_sources:\n    - watcher\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    enqueued = store.enqueue(
        issue=50,
        phase="draft",
        source="watcher",
        actor_kind="automation",
        priority="low",
        requested_by=["watcher"],
        requested_at=_NOW,
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
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    queue_plan = qmod._plan_with_window(
        store,
        client,
        now=now,
        window=seed_window(seed_path),
        extra_sources=seed_extra_sources(seed_path),
    )
    by_id = {e.request_id: e for e in queue_plan.entries}
    assert by_id[enqueued.request_id].reason == "window"


def test_undeclared_source_does_not_wait_outside_window(tmp_path, monkeypatch):
    """Refs #7329."""
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod
    from issuesmith.queue_triage import seed_extra_sources, seed_window

    seed_path = tmp_path / "night-queue.yaml"
    seed_path.write_text(
        "window:\n  start: '01:00'\n  end: '07:00'\n  extra_sources:\n    - watcher\n",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    enqueued = store.enqueue(
        issue=50,
        phase="draft",
        source="other-bot",
        actor_kind="automation",
        priority="low",
        requested_by=["other-bot"],
        requested_at=_NOW,
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
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    queue_plan = qmod._plan_with_window(
        store,
        client,
        now=now,
        window=seed_window(seed_path),
        extra_sources=seed_extra_sources(seed_path),
    )
    by_id = {e.request_id: e for e in queue_plan.entries}
    assert by_id[enqueued.request_id].reason != "window"


