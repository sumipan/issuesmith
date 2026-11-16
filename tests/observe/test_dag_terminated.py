"""Tests for DagTerminatedEvent detection, evaluate, and execute.

Fix 1 (nexus #4137): DagTerminatedEvent retains in_flight and removes only the -running label.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import list_open
from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe.dag_state import DagState
from issuesmith.observe.events import DagTerminatedEvent
from issuesmith.observe.policy import evaluate, execute
from issuesmith.queue_store import QueueStore


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'issuesmith.yaml'
    cfg_path.write_text(yaml.safe_dump({'repo': 'example/repo'}), encoding='utf-8')
    monkeypatch.setenv('ISSUESMITH_CONFIG', str(cfg_path))
    monkeypatch.setenv('GHDAG_FORGE', 'local')
    monkeypatch.setenv('GHDAG_FORGE_ROOT', str(tmp_path / 'forge'))
    reset_config_cache()
    store = QueueStore(queue_path=tmp_path / 'q.jsonl', state_path=tmp_path / 'state.json', lock_path=tmp_path / 'q.lock')
    client = get_forge()
    yield (store, client, tmp_path)
    reset_config_cache()

def _make_dirs(tmp_path: Path):
    done_dir = tmp_path / 'done'
    done_dir.mkdir(exist_ok=True)
    running_dir = tmp_path / 'running'
    running_dir.mkdir(exist_ok=True)
    return (done_dir, running_dir)

def _dag_failed_states(issue_num: int) -> dict[int, DagState]:
    """Return dag_states dict representing a failed DAG for the given issue."""
    key = f'issuesmith:impl:{issue_num}'
    return {issue_num: DagState(issue=issue_num, key=key, status='failed', failed_step='p2', failed_uuid='p2', failed_result_path='/jobs/done/p2')}

def _dag_running_states(issue_num: int) -> dict[int, DagState]:
    key = f'issuesmith:impl:{issue_num}'
    return {issue_num: DagState(issue=issue_num, key=key, status='running')}

def test_observe_releases_failed_dag_from_done_markers(env):
    """observe() with real exec.jsonl and done markers releases a failed DAG."""
    from dataclasses import replace

    from issuesmith.observe import observe
    store, client, tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace
    done_dir, _running_dir = _make_dirs(tmp_path)
    exec_path = tmp_path / 'exec.jsonl'
    issue_num = client.issue_create('title6', 'body')
    client.issue_update(issue_num, labels_add=[f'{ns}:draft-done', f'{ns}:develop-running'])
    store.add_in_flight(issue_num, 'claude', role='implementation')
    key = f'issuesmith:impl:{issue_num}'
    rows = [{'uuid': 'u-p1', 'idempotency_key': key, 'depends': [], 'annotations': {'step_name': 'p1'}}, {'uuid': 'u-p2', 'idempotency_key': key, 'depends': ['u-p1'], 'annotations': {'step_name': 'p2'}}]
    exec_path.write_text(''.join((json.dumps(r) + '\n' for r in rows)), encoding='utf-8')
    (done_dir / 'u-p1').write_text('0', encoding='utf-8')
    (done_dir / 'u-p2').write_text('1', encoding='utf-8')
    patched_cfg = replace(cfg, paths=replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir))

    def _tick():
        evts = [e for e in observe(store.snapshot(), client, patched_cfg) if isinstance(e, DagTerminatedEvent)]
        execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
        return evts
    evts = _tick()
    assert len(evts) == 1
    assert evts[0].failed_step == 'p2'
    assert evts[0].phase == 'develop'
    assert any((e.get('issue') == issue_num for e in store.snapshot().in_flight)), 'in_flight must be retained after DagTerminatedEvent (nexus #4137)'
    labels_after = {lbl['name'] if isinstance(lbl, dict) else str(lbl) for lbl in client.issue_get(issue_num, fields=['labels']).get('labels', [])}
    assert f'{ns}:develop-running' not in labels_after
    assert f'{ns}:develop-ready' not in labels_after
    assert f'{ns}:draft-done' in labels_after
    assert len(list_open(client)) == 1
    comments_first = client.get_issue_comments(issue_num)
    second_evts = _tick()
    assert len(second_evts) == 1
    assert isinstance(second_evts[0], DagTerminatedEvent)
    assert len(client.get_issue_comments(issue_num)) == len(comments_first)
