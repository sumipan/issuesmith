"""observe() reduced mode and api_brake transition events (#3769, AC-4 / AC-5 / AC-6)."""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from issuesmith.config import ApiBreakConfig, get_config, reset_config_cache
from issuesmith.observe import observe
from issuesmith.observe.dag_state import DagState
from issuesmith.observe.events import (
    DagTerminatedEvent,
    GitHubApiLowEvent,
    GitHubApiRecoveredEvent,
    OrphanExecEvent,
)
from issuesmith.observe.policy import AndonAction, HaltAction, evaluate
from issuesmith.queue_store import QueueStore

_NOW = datetime(2026, 9, 25, 4, 0, tzinfo=timezone.utc)


class _NoApiClient:
    """Any forge access fails the test: the reduced mode must not touch the API."""

    def __getattr__(self, name):
        raise AssertionError(f"forge API called in reduced mode: {name}")


class _CountingClient:
    def __init__(self):
        self.calls: list[str] = []

    def issue_get(self, number, fields=None):
        self.calls.append("issue_get")
        return {"number": number, "state": "OPEN", "labels": []}

    def list_issues(self, label, state="open"):
        self.calls.append("list_issues")
        return []


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    base = get_config()
    jobs = tmp_path / "jobs"
    (jobs / "done").mkdir(parents=True)
    paths = dataclasses.replace(
        base.paths,
        exec_jsonl=jobs / "exec.jsonl",
        done_dir=jobs / "done",
        quota_state=jobs / "quota-gate.json",
        metrics=jobs / "metrics.jsonl",
    )
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "q.lock",
    )

    def config(*, enabled: bool):
        return dataclasses.replace(
            base, paths=paths, api_brake=ApiBreakConfig(enabled=enabled, min_remaining=800),
        )

    yield store, jobs, config
    reset_config_cache()


def _write_rate_limit(jobs: Path, remaining: int, reset: datetime) -> None:
    record = {
        "event": "github_rate_limit",
        "timestamp": "2026-09-25T03:59:00+00:00",
        "remaining": remaining,
        "limit": 5000,
        "reset": int(reset.timestamp()),
    }
    with (jobs / "audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def _setup_failed_and_orphan(store: QueueStore, jobs: Path, monkeypatch) -> None:
    """#100 is in_flight with a failed DAG; #200 has an exec row but no in_flight entry."""
    store.add_in_flight(100, "claude", role="implementation")
    (jobs / "exec.jsonl").write_text(
        json.dumps({"uuid": "orphan-uuid", "idempotency_key": "issuesmith:impl:200"}) + "\n",
        encoding="utf-8",
    )
    states = {
        100: DagState(
            issue=100,
            key="issuesmith:impl:100:2",
            status="failed",
            failed_step="p2",
            failed_uuid="p2-uuid",
            failed_result_path="/jobs/done/p2-uuid",
        ),
    }
    monkeypatch.setattr("issuesmith.observe.load_dag_states", lambda *a, **k: states)


def test_ac4_reduced_mode_detects_without_api(env, monkeypatch):
    store, jobs, config = env
    _setup_failed_and_orphan(store, jobs, monkeypatch)

    events = observe(
        store.snapshot(), _NoApiClient(), config(enabled=False),
        now=_NOW, github_api_low=True,
    )

    assert DagTerminatedEvent(
        issue=100,
        key="issuesmith:impl:100:2",
        phase="",
        failed_step="p2",
        failed_uuid="p2-uuid",
        result_path="/jobs/done/p2-uuid",
    ) in events
    assert OrphanExecEvent(uuid="orphan-uuid", issue=200) in events
    assert {e.kind for e in events} == {"dag_terminated", "orphan_exec"}


def test_ac4_reduced_mode_from_audit_when_brake_enabled(env, monkeypatch):
    store, jobs, config = env
    _setup_failed_and_orphan(store, jobs, monkeypatch)
    _write_rate_limit(jobs, 100, _NOW + timedelta(minutes=30))

    events = observe(store.snapshot(), _NoApiClient(), config(enabled=True), now=_NOW)

    assert {e.kind for e in events} == {"dag_terminated", "orphan_exec", "github_api_low"}


def test_ac5_transition_events_fire_once(env):
    store, jobs, config = env
    cfg = config(enabled=True)
    client = _CountingClient()
    reset = _NOW + timedelta(minutes=30)

    def api_events():
        events = observe(store.snapshot(), client, cfg, now=_NOW)
        return [e for e in events if e.kind.startswith("github_api")]

    assert api_events() == []  # no audit record yet

    _write_rate_limit(jobs, 500, reset)
    assert api_events() == [GitHubApiLowEvent(remaining=500)]
    assert api_events() == []
    _write_rate_limit(jobs, 300, reset)
    assert api_events() == []

    _write_rate_limit(jobs, 4000, reset)
    assert api_events() == [GitHubApiRecoveredEvent()]
    assert api_events() == []

    _write_rate_limit(jobs, 100, reset)
    assert api_events() == [GitHubApiLowEvent(remaining=100)]


def test_ac6_disabled_brake_keeps_full_observe(env):
    store, jobs, config = env
    store.add_in_flight(100, "claude", role="implementation")
    _write_rate_limit(jobs, 1, _NOW + timedelta(minutes=30))
    client = _CountingClient()

    events = observe(store.snapshot(), client, config(enabled=False), now=_NOW)

    assert not any(e.kind.startswith("github_api") for e in events)
    assert "list_issues" in client.calls
    assert "issue_get" in client.calls
    assert not (jobs / "quota-gate.json").exists()


def test_policy_notifies_without_halt():
    actions = evaluate(
        [GitHubApiLowEvent(remaining=321), GitHubApiRecoveredEvent()],
        get_config().observe,
    )
    assert not any(isinstance(a, HaltAction) for a in actions)
    assert actions == [
        AndonAction(
            kind="blocked",
            issue=0,
            summary="GitHub API rate limit low: 321 remaining",
            key="github_api_low",
        ),
        AndonAction(
            kind="recovered",
            issue=0,
            summary="GitHub API rate limit recovered",
            key="github_api_recovered",
        ),
    ]
