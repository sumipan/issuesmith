"""Queue stop: release in_flight and dequeue requests when the line stops."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import Andon, raise_andon
from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe.events import DagTerminatedEvent
from issuesmith.observe.policy import evaluate, execute
from issuesmith.queue_store import QueueStore
from tests.test_queue_concurrency import _JST, _NOW, _DispatchClient, _patch_paths, _store


def _make_store(tmp_path):
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


class _CommentClient:
    def __init__(self, labels=None):
        self.issue_comments: list[tuple[int, str]] = []
        self.issue_updates: list[tuple[int, dict]] = []
        self._labels = labels or []

    def issue_comment(self, issue: int, body: str) -> dict:
        self.issue_comments.append((issue, body))
        return {"id": 1, "body": body}

    def issue_get(self, issue, fields=None):
        return {"number": issue, "state": "OPEN", "labels": [{"name": x} for x in self._labels]}

    def get_issue_comments(self, issue):
        return [{"body": body} for _issue, body in self.issue_comments if _issue == issue]

    def list_issues(self, **kwargs):
        return []

    def issue_update(self, issue, **kwargs):
        self.issue_updates.append((issue, kwargs))
        return {}


@pytest.fixture
def issuesmith_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    payload = {
        "repo": "owner/repo",
        "label_namespace": "issuesmith",
        "timezone": "Asia/Tokyo",
        "supported_repos": ["owner/repo"],
        "concurrency": {"default": 1, "per_engine": {"claude": 1}},
        "paths": {
            "queue": "jobs/issuesmith-queue.jsonl",
            "queue_state": "logs/issuesmith-queue-state.json",
            "queue_lock": "logs/issuesmith-queue.lock",
            "exec_jsonl": "jobs/exec.jsonl",
            "done_dir": "jobs/done",
            "engine_state": ".pipeline-state/issuesmith-engine.yml",
        },
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "b1",
                "handler": "brushup",
            },
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "p1",
                "handler": "impl",
                "preconditions": ["draft-done"],
            },
        ],
    }
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield cfg_path
    reset_config_cache()


def test_andon_releases_in_flight_and_unblocks_conflicting_request(
    tmp_path, monkeypatch, issuesmith_config
):
    """refs #7464 AC-1."""
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 1}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    body = (
        "```yaml\n"
        "target_repo: owner/repo\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/a.py"\n'
        "```\n"
    )
    body2 = (
        "```yaml\n"
        "target_repo: owner/repo\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/a.py"\n'
        "```\n"
    )
    store = _store(tmp_path)
    store.add_in_flight(
        10,
        "claude",
        role="implementation",
        phase="develop",
        allow_paths=("src/a.py",),
        target_repo="owner/repo",
    )
    store.enqueue(
        issue=11,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            10: {"state": "OPEN", "labels": [], "body": body},
            11: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": body2,
            },
        }
    )
    andon = Andon(
        id="wf:10:p1:0",
        kind="blocked",
        issue=10,
        step="p1",
        summary="stop",
        evidence="",
        options=["resume"],
        default="resume",
        mention="",
    )
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    with patch("issuesmith.andon.QueueStore", return_value=store), patch(
        "issuesmith.andon._write_metrics"
    ):
        raise_andon(client, andon, metrics_path=tmp_path / "m.jsonl")
    assert store.snapshot().in_flight == []
    result = qmod.dispatch_one(
        now=datetime(2026, 9, 5, 12, 0, tzinfo=_JST),
        client=client,
        store=store,
        skip_seed=True,
    )
    assert result.dispatched and result.issue == 11


@pytest.fixture
def _observe_env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "owner/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "q.lock",
    )
    client = get_forge()
    yield store, client, tmp_path
    reset_config_cache()


def test_dag_terminated_releases_in_flight(_observe_env):
    """refs #7464 AC-2; observe dag_terminated retains in_flight (nexus #4137)."""
    store, client, tmp_path = _observe_env
    issue_num = client.issue_create("title", "body")
    store.add_in_flight(issue_num, "claude", role="implementation")
    ev = DagTerminatedEvent(
        issue=issue_num,
        key=f"{issue_num}:develop:p1:0",
        phase="develop",
        failed_step="p1",
    )
    actions = evaluate([ev], get_config().observe)
    with (
        patch("issuesmith.andon.QueueStore", return_value=store),
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon.project_issue"),
    ):
        execute(actions, store, sinks=[], client=client)
    assert any(e.get("issue") == issue_num for e in store.snapshot().in_flight)
    comments = client.get_issue_comments(issue_num)
    assert any("wf:" in str(c.get("body", "")) or "observe:" in str(c.get("body", "")) for c in comments)


def test_andon_dequeues_pending_requests_without_comment(tmp_path):
    """refs #7464 AC-3."""
    store = _make_store(tmp_path)
    store.enqueue(
        issue=10,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
    )
    store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
    )
    rids = list(store.snapshot().active_order)
    client = _CommentClient()
    andon = Andon(
        id="wf:10:p1:0",
        kind="blocked",
        issue=10,
        step="p1",
        summary="stop",
        evidence="",
        options=["resume"],
        default="resume",
        mention="",
    )
    with patch("issuesmith.andon.QueueStore", return_value=store), patch(
        "issuesmith.andon.project_issue", return_value=None
    ), patch("issuesmith.andon._write_metrics"):
        raise_andon(client, andon, metrics_path=tmp_path / "m.jsonl")
    assert len(client.issue_comments) == 1
    snap = store.snapshot()
    assert not snap.active_order
    for rid in rids:
        meta = snap.request_meta[rid]
        assert meta["outcome"] == "dequeued"
        assert meta["reason"] == "stopped: andon wf:10:p1:0"


def test_tick_releases_in_flight_of_issue_with_andon_label(tmp_path, monkeypatch):
    """refs #7464 AC-6."""
    import json as _json

    from issuesmith import queue as qmod
    store = _make_store(tmp_path)
    store.add_in_flight(7, "claude")
    state = _json.loads(store.state_path.read_text(encoding="utf-8"))
    state["in_flight"][0]["held"] = {"by": "wf:7:p1:0", "at": "2026-01-01T00:00:00+09:00"}
    store.state_path.write_text(_json.dumps(state), encoding="utf-8")
    ns = get_config().label_namespace
    label = f"{ns}:andon-decision"

    class _Client:
        def issue_get(self, issue, fields=None):
            return {"number": issue, "state": "OPEN", "labels": [{"name": label}]}

        def list_issues(self, **kwargs):
            return []

        def get_issue_comments(self, issue):
            from issuesmith.andon import to_comment

            a = Andon(
                id="wf:7:p1:0",
                kind="decision",
                issue=7,
                step="p1",
                summary="s",
                evidence="",
                options=["resume"],
                default="resume",
                mention="",
            )
            return [{"body": to_comment(a), "created_at": _NOW}]

    client = _Client()
    qmod._release_finished_in_flight(store, client, [])
    assert store.snapshot().in_flight == []


def test_status_lists_stopped_issues(tmp_path, monkeypatch, capsys):
    """refs #7464 AC-7."""
    from issuesmith import queue as qmod

    store = _make_store(tmp_path)
    andon = Andon(
        id="wf:10:p1:0",
        kind="blocked",
        issue=10,
        step="p1",
        summary="s",
        evidence="",
        options=["resume"],
        default="resume",
        mention="",
    )
    mock_client = MagicMock()

    args = argparse.Namespace(
        queue_path=None,
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        json=False,
    )
    with patch("issuesmith.queue.get_forge", return_value=mock_client), patch(
        "issuesmith.andon.list_open", return_value=[andon]
    ), patch.object(qmod, "plan", return_value=qmod.QueuePlan(revision=0, entries=(), halt=False)):
        monkeypatch.setattr(
            qmod,
            "QueueStore",
            lambda queue_path=None, state_path=None, lock_path=None, **kw: store,
        )
        qmod._cmd_status(args)
    out = capsys.readouterr().out
    assert "stopped issue=#10 andon=wf:10:p1:0" in out

    args.json = True
    buf = StringIO()
    with patch("issuesmith.queue.get_forge", return_value=mock_client), patch(
        "issuesmith.andon.list_open", return_value=[andon]
    ), patch.object(qmod, "plan", return_value=qmod.QueuePlan(revision=0, entries=(), halt=False)):
        monkeypatch.setattr(
            qmod,
            "QueueStore",
            lambda queue_path=None, state_path=None, lock_path=None, **kw: store,
        )
        with patch("sys.stdout", buf):
            qmod._cmd_status(args)
    payload = json.loads(buf.getvalue())
    assert payload["stopped"] == [{"issue": 10, "andon": "wf:10:p1:0"}]
