"""Auto-resolve of observe andons when condition clears (sumipan/nexus#3740).

Fix 1 (nexus #4137): DagTerminatedEvent retains in_flight; andon stays open while
DAG is still failing (event keeps firing) and auto-resolves when DAG recovers (event clears).
"""
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


# AC-1: dag_terminated andon is auto-resolved when DAG recovers (event clears, issue in_flight)
def test_dag_terminated_auto_resolved_when_dag_recovers(store, client):
    """dag_terminated andon is answered when the DAG recovers (event stops firing).

    Fix 1 (nexus #4137): in_flight is retained after DagTerminatedEvent. Auto-resolve
    fires on the first tick where the event is absent (= DAG recovered or new run started),
    because the issue is still in_flight.
    """
    number = client.issue_create("my issue", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    # in_flight is RETAINED (RemoveRunningLabelAction only removes the label); andon is now open

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"
    assert any(a.id == dag_id for a in list_open(client)), "andon should be open after first tick"

    # Next tick: no dag_terminated events (DAG has recovered / new run started)
    execute([], store, sinks=[], client=client)

    open_andons = list_open(client)
    assert not any(a.id == dag_id for a in open_andons), (
        "dag_terminated andon should be auto-resolved when DAG recovers (event clears)"
    )


# AC-1: while DAG is still failing, dag_terminated andon stays open across ticks
def test_dag_terminated_stays_open_while_dag_still_failing(store, client):
    """While the DAG is still failed, dag_terminated andon is NOT prematurely auto-resolved.

    Fix 1 (nexus #4137): each tick with a failed DAG re-fires DagTerminatedEvent, keeping
    the andon in the active set and preventing spurious auto-resolve.
    """
    number = client.issue_create("stuck issue", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    # in_flight retained; andon open

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"

    # Second tick: DAG still failed → DagTerminatedEvent fires again → should NOT auto-resolve
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)

    open_andons = list_open(client)
    assert any(a.id == dag_id for a in open_andons), (
        "dag_terminated andon should remain open while DAG is still failing"
    )


# AC-1: after multiple failing ticks, andon resolves when DAG finally recovers
def test_dag_terminated_auto_resolved_after_multiple_failed_ticks(store, client):
    """After several failing ticks, andon auto-resolves on the first tick where event clears.

    Fix 1 (nexus #4137): event keeps firing while DAG is still failed, then clears when
    DAG recovers → auto-resolve (issue is still in_flight throughout).
    """
    number = client.issue_create("late recovery", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)

    dag_id = f"observe:{number}:dag_terminated:{number}:develop:cp1:0"

    # Ticks while DAG is still failing
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    assert any(a.id == dag_id for a in list_open(client))

    # DAG recovers: event clears → auto-resolve fires (issue still in_flight)
    execute([], store, sinks=[], client=client)

    assert not any(a.id == dag_id for a in list_open(client))


# AC-1: after DAG recovery + auto-resolve, a new failure raises the andon again
def test_dag_terminated_recurs_after_auto_resolve(store, client):
    """After auto-resolve (DAG recovered), a new DAG failure raises the andon again.

    Fix 1 (nexus #4137): in_flight is retained throughout; no add_in_flight needed
    between the recovery and the re-failure.
    """
    number = client.issue_create("recur issue", "body")
    store.add_in_flight(number, "claude", role="implementation")
    execute(_dag_terminated_actions(number, gen=0), store, sinks=[], client=client)
    # in_flight retained; andon raised

    # DAG recovers: no events → auto-resolve fires (issue still in_flight)
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
