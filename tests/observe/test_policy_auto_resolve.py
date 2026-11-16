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
from issuesmith.observe.dag_state import DagState
from issuesmith.observe.events import DagTerminatedEvent
from issuesmith.observe.policy import evaluate, execute
from issuesmith.queue_store import QueueStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> QueueStore:
    cfg_path = tmp_path / 'issuesmith.yaml'
    cfg_path.write_text(yaml.safe_dump({'repo': 'example/repo'}), encoding='utf-8')
    monkeypatch.setenv('ISSUESMITH_CONFIG', str(cfg_path))
    reset_config_cache()
    yield QueueStore(queue_path=tmp_path / 'q.jsonl', state_path=tmp_path / 'state.json', lock_path=tmp_path / 'q.lock')
    reset_config_cache()

@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('GHDAG_FORGE', 'local')
    monkeypatch.setenv('GHDAG_FORGE_ROOT', str(tmp_path / 'forge'))
    return get_forge()

def _dag_terminated_actions(issue: int, phase: str='develop', step: str='cp1', gen: int=0):
    key = f'{issue}:{phase}:{step}:{gen}'
    return evaluate([DagTerminatedEvent(issue=issue, key=key, phase=phase, failed_step=step)], get_config().observe)

def _patch_dag_state(monkeypatch, issue: int, status: str):
    monkeypatch.setattr('issuesmith.observe.policy.load_dag_states', lambda *_a, **_k: {issue: DagState(issue=issue, key='k', status=status)})

def test_dag_terminated_auto_resolved_after_multiple_failed_ticks(store, client, monkeypatch):
    """After several failing ticks, andon auto-resolves on the first tick where event clears.

    Fix 1 (nexus #4137): event keeps firing while DAG is still failed, then clears when
    DAG recovers → auto-resolve (issue is still in_flight throughout).
    """
    number = client.issue_create('late recovery', 'body')
    store.add_in_flight(number, 'claude', role='implementation')
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    dag_id = f'observe:{number}:dag_terminated:{number}:develop:cp1:0'
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    assert any((a.id == dag_id for a in list_open(client)))
    _patch_dag_state(monkeypatch, number, 'succeeded')
    execute([], store, sinks=[], client=client)
    assert not any((a.id == dag_id for a in list_open(client)))

def test_answer_if_open_no_exception_when_not_found(client):
    from issuesmith.andon import answer_if_open
    answer_if_open(client, 'observe:999:dag_terminated:999:develop:cp1:0', 'auto-resolved: condition cleared')

def test_answer_if_open_noop_when_client_is_none():
    from issuesmith.andon import answer_if_open
    answer_if_open(None, 'observe:999:dag_terminated:999:develop:cp1:0', 'auto-resolved: condition cleared')

def test_dag_terminated_not_auto_resolved_when_still_failed(store, client, monkeypatch):
    number = client.issue_create('still failed', 'body')
    store.add_in_flight(number, 'claude', role='implementation')
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    dag_id = f'observe:{number}:dag_terminated:{number}:develop:cp1:0'
    _patch_dag_state(monkeypatch, number, 'failed')
    execute([], store, sinks=[], client=client)
    assert any((a.id == dag_id for a in list_open(client)))

def test_dag_terminated_suppressed_by_pipeline_andon_strips_running_label(store, client):
    """#6328: pipeline andon open → no dag_terminated post, running label still removed."""
    number = client.issue_create('suppressed dag term', 'body')
    ns = get_config().label_namespace
    client.issue_update(number, labels_add=[f'{ns}:develop-running'])
    pipeline = Andon(id=f'issuesmith:{number}:cp2:0', kind='blocked', issue=number, step='cp2', summary='cp2 failed')
    raise_andon(client, pipeline)
    execute(_dag_terminated_actions(number), store, sinks=[], client=client)
    dag_id = f'observe:{number}:dag_terminated:{number}:develop:cp1:0'
    assert not any((a.id == dag_id for a in list_open(client)))
    labels = client.issue_get(number).get('labels') or []
    label_names = [lbl.get('name') if isinstance(lbl, dict) else lbl for lbl in labels]
    assert f'{ns}:develop-running' not in label_names
