"""Auto-resolve of observe andons when condition clears (sumipan/nexus#3740)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import Andon, list_open, raise_andon
from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe.events import DagTerminatedEvent, OrphanExecEvent
from issuesmith.observe.policy import evaluate, execute
from issuesmith.queue_store import QueueStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> QueueStore:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "q.lock",
    )
    reset_config_cache()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    return get_forge()


def _dag_terminated_actions(issue: int, phase: str = "develop", step: str = "cp1", gen: int = 0):
    key = f"{issue}:{phase}:{step}:{gen}"
    return evaluate(
        [DagTerminatedEvent(issue=issue, key=key, phase=phase, failed_step=step)],
        get_config().observe,
    )


def _orphan_actions(uuid: str, issue: int = 0):
    return evaluate(
        [OrphanExecEvent(uuid=uuid, issue=issue or None)],
        get_config().observe,
    )


# AC-1: dag_terminated andon is auto-resolved when issue is re-dispatched (in_flight again)
def test_dag_terminated_auto_resolved_when_issue_redispatched(store, client):
    """dag_terminated andon is answered when the issue is back in_flight on the next tick."""
    number = client.issue_create("my issue", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    # in_flight was cleared by ReleaseInFlightAction; andon is now open

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"
    assert any(a.id == dag_id for a in list_open(client)), "andon should be open after first tick"

    # Simulate user re-dispatching: issue goes back into in_flight (new run started)
    store.add_in_flight(number, "claude", role="implementation")

    # Next tick: no dag_terminated events (new run hasn't failed yet)
    execute([], store, sinks=[], client=client)

    open_andons = list_open(client)
    assert not any(a.id == dag_id for a in open_andons), (
        "dag_terminated andon should be auto-resolved when issue is re-dispatched"
    )


# AC-1: without re-dispatch, dag_terminated andon stays open across ticks
def test_dag_terminated_stays_open_without_redispatch(store, client):
    """Without re-dispatch, dag_terminated andon is NOT prematurely auto-resolved."""
    number = client.issue_create("stuck issue", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    # in_flight cleared by ReleaseInFlightAction; andon open

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"

    # Second tick: no events, issue NOT in_flight → should NOT auto-resolve
    execute([], store, sinks=[], client=client)

    open_andons = list_open(client)
    assert any(a.id == dag_id for a in open_andons), (
        "dag_terminated andon should remain open until issue is re-dispatched"
    )


# AC-1: re-dispatch several ticks after the failure still auto-resolves the andon
def test_dag_terminated_auto_resolved_when_redispatched_after_idle_ticks(store, client):
    number = client.issue_create("late redispatch", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"

    # Idle ticks while the user has not re-dispatched yet
    execute([], store, sinks=[], client=client)
    execute([], store, sinks=[], client=client)
    assert any(a.id == dag_id for a in list_open(client))

    store.add_in_flight(number, "claude", role="implementation")
    execute([], store, sinks=[], client=client)

    assert not any(a.id == dag_id for a in list_open(client))


# AC-1: after redispatch and auto-resolve, re-occurrence raises andon again
def test_dag_terminated_recurs_after_auto_resolve(store, client):
    number = client.issue_create("recur issue", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number, gen=0), store, sinks=[], client=client)

    # Simulate re-dispatch
    store.add_in_flight(number, "claude", role="implementation")

    # Next tick with no events: auto-resolve fires (issue is in_flight)
    execute([], store, sinks=[], client=client)

    # Issue fails again at same step (e.g. generation 4)
    execute(_dag_terminated_actions(number, gen=4), store, sinks=[], client=client)

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"
    comments = client.get_issue_comments(number)
    # 1 raise + 1 auto-resolve answer + 1 re-raise = 3 comments
    assert len([c for c in comments if "auto-resolved" in c.get("body", "")]) == 1
    assert any(a.id == dag_id for a in list_open(client))


# AC-2: orphan andon is auto-resolved when UUID disappears from running
def test_orphan_auto_resolved_when_uuid_removed(store, client):
    number = client.issue_create("orphan issue", "body")
    uuid = "dead-beef-1234"
    execute(_orphan_actions(uuid, issue=number), store, sinks=[], client=client)

    orphan_id = f"observe:{number}:orphan:{uuid}:0"
    assert any(a.id == orphan_id for a in list_open(client)), "orphan andon should be raised"

    # UUID is gone from running (empty events in next tick)
    execute([], store, sinks=[], client=client)

    open_andons = list_open(client)
    assert not any(a.id == orphan_id for a in open_andons), (
        "orphan andon should be auto-resolved when UUID disappears"
    )


# AC-4: decision andon raised outside observe policy is not auto-resolved
def test_decision_andon_raised_outside_observe_not_auto_resolved(store, client):
    number = client.issue_create("decision issue", "body")
    decision_andon = Andon(
        id=f"observe:{number}:skew:some-pkg:0",
        kind="decision",
        issue=number,
        step="observe",
        summary="version skew detected",
    )
    raise_andon(client, decision_andon)

    # observe tick with no active observe andons
    execute([], store, sinks=[], client=client)

    open_andons = list_open(client)
    assert any(a.id == decision_andon.id for a in open_andons), (
        "decision andon raised outside observe execute() should not be auto-resolved"
    )


# AC-7: answer_if_open does not raise KeyError when andon is not found
def test_answer_if_open_no_exception_when_not_found(client):
    from issuesmith.andon import answer_if_open
    # Should not raise
    answer_if_open(client, "observe:999:dag_terminated:999:develop:cp1:0", "auto-resolved: condition cleared")


# AC-7: answer_if_open with client=None is a no-op
def test_answer_if_open_noop_when_client_is_none():
    from issuesmith.andon import answer_if_open
    answer_if_open(None, "observe:999:dag_terminated:999:develop:cp1:0", "auto-resolved: condition cleared")
