"""dispatch_one holds new work back while the GitHub API budget is low (#3769)."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from issuesmith.config import ApiBreakConfig, get_config
from issuesmith.queue_store import QueueStore

_NOW = datetime(2026, 9, 25, 13, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
_ENQUEUED_AT = datetime.now().astimezone().isoformat()


class _Client:
    """Records every forge call; issue_get always 404s so a dispatch attempt is visible."""

    def __init__(self):
        self.calls: list[str] = []

    def issue_get(self, number, fields=None):
        from ghdag.core.exceptions import GitHubApiError

        self.calls.append("issue_get")
        raise GitHubApiError(f"issue #{number} not found", status_code=404)

    def issue_comment(self, number, body):
        self.calls.append("issue_comment")

    def get_issue_comments(self, number):
        self.calls.append("get_issue_comments")
        return []

    def list_issues(self, label, state="open"):
        self.calls.append("list_issues")
        return []

    def list_open_issues_for_queue(self):
        self.calls.append("list_open_issues_for_queue")
        return []

    def api_request(self, path, **kwargs):
        self.calls.append("api_request")
        return []


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    from issuesmith import queue as qmod

    jobs = tmp_path / "jobs"
    jobs.mkdir()
    base = get_config()

    def configure(*, enabled: bool, min_remaining: int = 800):
        cfg = dataclasses.replace(
            base,
            paths=dataclasses.replace(base.paths, exec_jsonl=jobs / "exec.jsonl"),
            api_brake=ApiBreakConfig(enabled=enabled, min_remaining=min_remaining),
        )
        monkeypatch.setattr(qmod, "get_config", lambda: cfg)
        return cfg

    store = QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    rid = store.enqueue(
        issue=4242,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_ENQUEUED_AT,
    ).request_id
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    milestone_calls: list[int] = []
    monkeypatch.setattr(
        qmod, "advance_milestone_chains", lambda *a, **k: milestone_calls.append(1),
    )
    return qmod, store, rid, jobs, configure, milestone_calls


def _write_rate_limit(jobs: Path, remaining: int, reset: datetime) -> None:
    record = {
        "event": "github_rate_limit",
        "timestamp": "2026-09-25T03:59:00+00:00",
        "remaining": remaining,
        "limit": 5000,
        "reset": int(reset.timestamp()),
        "used": 5000 - remaining,
        "correlation_id": None,
    }
    (jobs / "audit.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _dispatch(qmod, store, client):
    return qmod.dispatch_one(
        now=_NOW,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )


def test_ac1_low_budget_holds_dispatch(env):
    qmod, store, rid, jobs, configure, milestone_calls = env
    configure(enabled=True, min_remaining=800)
    reset = _NOW + timedelta(minutes=20)
    _write_rate_limit(jobs, 500, reset)
    client = _Client()

    out = _dispatch(qmod, store, client)

    assert out.dispatched is False
    assert out.reason.startswith("github_api_low")
    assert "remaining=500" in out.reason
    assert f"reset={reset.strftime('%H:%M')}" in out.reason
    # No forge calls and no milestone advance while braked; the request stays queued.
    assert client.calls == []
    assert milestone_calls == []
    assert rid in store.snapshot().active_order


def test_ac2_past_reset_does_not_brake(env):
    qmod, store, rid, jobs, configure, milestone_calls = env
    configure(enabled=True, min_remaining=800)
    _write_rate_limit(jobs, 10, _NOW.astimezone(timezone.utc) - timedelta(minutes=1))
    client = _Client()

    out = _dispatch(qmod, store, client)

    assert "github_api_low" not in out.reason
    assert "issue_get" in client.calls
    assert milestone_calls == [1]


def test_ac3_missing_audit_does_not_brake(env):
    qmod, store, rid, jobs, configure, milestone_calls = env
    configure(enabled=True)
    client = _Client()

    out = _dispatch(qmod, store, client)

    assert "github_api_low" not in out.reason
    assert "issue_get" in client.calls


def test_enough_budget_does_not_brake(env):
    qmod, store, rid, jobs, configure, milestone_calls = env
    configure(enabled=True, min_remaining=800)
    _write_rate_limit(jobs, 800, _NOW + timedelta(minutes=20))
    client = _Client()

    out = _dispatch(qmod, store, client)

    assert "github_api_low" not in out.reason
    assert "issue_get" in client.calls


def test_ac6_disabled_brake_ignores_low_budget(env):
    qmod, store, rid, jobs, configure, milestone_calls = env
    configure(enabled=False)
    _write_rate_limit(jobs, 1, _NOW + timedelta(minutes=20))
    client = _Client()

    out = _dispatch(qmod, store, client)

    assert "github_api_low" not in out.reason
    assert "issue_get" in client.calls
    assert milestone_calls == [1]


def test_default_config_has_brake_disabled(tmp_path):
    from issuesmith.config import load_config

    path = tmp_path / "issuesmith.yaml"
    path.write_text("repo: example/repo\n", encoding="utf-8")
    assert load_config(path).api_brake == ApiBreakConfig(enabled=False, min_remaining=800)


def test_api_brake_section_is_parsed(tmp_path):
    from issuesmith.config import load_config

    path = tmp_path / "issuesmith.yaml"
    path.write_text(
        "repo: example/repo\napi_brake:\n  enabled: true\n  min_remaining: 300\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.api_brake == ApiBreakConfig(enabled=True, min_remaining=300)
