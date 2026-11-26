"""dag_terminated detection for observe.workflows (nexus #5393)."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe import _detect_dag_terminated_workflows, _detect_orphan_exec
from issuesmith.observe.dag_state import DagState, load_dag_states
from issuesmith.observe.events import DagTerminatedEvent
from issuesmith.observe.policy import AndonAction, RemoveRunningLabelAction, evaluate, execute
from issuesmith.queue_store import QueueStore


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / 'issuesmith.yaml'
    cfg_path.write_text(yaml.safe_dump({'repo': 'example/repo', 'observe': {'workflows': [{'name': 'bump'}, {'name': 'wsl'}]}}), encoding='utf-8')
    monkeypatch.setenv('ISSUESMITH_CONFIG', str(cfg_path))
    monkeypatch.setenv('GHDAG_FORGE', 'local')
    monkeypatch.setenv('GHDAG_FORGE_ROOT', str(tmp_path / 'forge'))
    reset_config_cache()
    store = QueueStore(queue_path=tmp_path / 'q.jsonl', state_path=tmp_path / 'state.json', lock_path=tmp_path / 'q.lock')
    client = get_forge()
    yield (store, client, tmp_path)
    reset_config_cache()

def _dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    done_dir = tmp_path / 'done'
    done_dir.mkdir(exist_ok=True)
    running_dir = tmp_path / 'running'
    running_dir.mkdir(exist_ok=True)
    exec_path = tmp_path / 'exec.jsonl'
    return (exec_path, done_dir, running_dir)

def _cfg_with_paths(tmp_path: Path, exec_path: Path, done_dir: Path) -> object:
    cfg = get_config()
    return replace(cfg, paths=replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir))

def _failed_bump_dag(exec_path: Path, done_dir: Path, issue_num: int, *, generation: int=1) -> None:
    key = f'bump:bump:{issue_num}:{generation}'
    rows = [{'uuid': 'b1', 'idempotency_key': key, 'depends': [], 'annotations': {'step_name': 'bump'}}, {'uuid': 'b2', 'idempotency_key': key, 'depends': ['b1'], 'annotations': {'step_name': 'post'}}]
    exec_path.write_text('\n'.join((json.dumps(r) for r in rows)) + '\n', encoding='utf-8')
    (done_dir / 'b1').write_text('0', encoding='utf-8')
    (done_dir / 'b2').write_text('1', encoding='utf-8')

def test_bump_running_label_emits_namespaced_event(env):
    store, client, tmp_path = env
    exec_path, done_dir, running_dir = _dirs(tmp_path)
    cfg = _cfg_with_paths(tmp_path, exec_path, done_dir)
    issue_num = client.issue_create('bump stuck', 'body')
    client.issue_update(issue_num, labels_add=['bump:running'])
    _failed_bump_dag(exec_path, done_dir, issue_num)
    events = _detect_dag_terminated_workflows(client, cfg, cfg.observe.max_api_calls)
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, DagTerminatedEvent)
    assert ev.namespace == 'bump'
    assert ev.issue == issue_num
    assert ev.phase == ''
    actions = evaluate([ev], cfg.observe)
    assert not any((isinstance(a, RemoveRunningLabelAction) for a in actions))
    andon_actions = [a for a in actions if isinstance(a, AndonAction)]
    assert len(andon_actions) == 1
    assert andon_actions[0].namespace == 'bump'
    assert andon_actions[0].kind == 'blocked'

def test_wsl_develop_running_sets_phase(env):
    store, client, tmp_path = env
    exec_path, done_dir, running_dir = _dirs(tmp_path)
    cfg = _cfg_with_paths(tmp_path, exec_path, done_dir)
    issue_num = client.issue_create('wsl stuck', 'body')
    client.issue_update(issue_num, labels_add=['wsl:develop-running'])
    key = f'wsl:develop:{issue_num}'
    rows = [{'uuid': 'd1', 'idempotency_key': key, 'depends': [], 'annotations': {'step_name': 'p1'}}, {'uuid': 'd2', 'idempotency_key': key, 'depends': ['d1'], 'annotations': {'step_name': 'p2'}}]
    exec_path.write_text('\n'.join((json.dumps(r) for r in rows)) + '\n', encoding='utf-8')
    (done_dir / 'd1').write_text('0', encoding='utf-8')
    (done_dir / 'd2').write_text('1', encoding='utf-8')
    events = _detect_dag_terminated_workflows(client, cfg, cfg.observe.max_api_calls)
    assert len(events) == 1
    assert events[0].phase == 'develop'
    assert events[0].namespace == 'wsl'

def test_bump_dag_terminated_auto_resolves_without_in_flight(env, monkeypatch):
    store, client, tmp_path = env
    issue_num = client.issue_create('bump auto', 'body')
    client.issue_update(issue_num, labels_add=['bump:running'])
    ev = DagTerminatedEvent(issue=issue_num, key=f'bump:bump:{issue_num}', failed_step='bump', failed_uuid='b2', result_path='/jobs/done/b2', namespace='bump')
    execute(evaluate([ev], get_config().observe), store, sinks=[], client=client)
    labels = {lbl['name'] if isinstance(lbl, dict) else str(lbl) for lbl in client.issue_get(issue_num, fields=['labels']).get('labels', [])}
    assert 'bump:andon-blocked' in labels
    monkeypatch.setattr('issuesmith.observe.policy.load_dag_states', lambda *_a, **_k: {issue_num: DagState(issue=issue_num, key='k', status='running')})
    execute([], store, sinks=[], client=client)
    labels_after = {lbl['name'] if isinstance(lbl, dict) else str(lbl) for lbl in client.issue_get(issue_num, fields=['labels']).get('labels', [])}
    assert 'bump:andon-blocked' not in labels_after

def test_orphan_exec_covers_enumerated_workflow_running_guard(env):
    store, client, tmp_path = env
    exec_path, done_dir, running_dir = _dirs(tmp_path)
    cfg = _cfg_with_paths(tmp_path, exec_path, done_dir)
    issue_num = 5400
    key = f'bump:bump:{issue_num}'
    exec_path.write_text(json.dumps({'uuid': 'orphan-u', 'idempotency_key': key, 'depends': []}) + '\n', encoding='utf-8')
    (running_dir / 'orphan-u.json').write_text('{}', encoding='utf-8')
    dag_states = load_dag_states(exec_path, done_dir, running_dir, workflow='bump')
    snap = store.snapshot()
    orphans = _detect_orphan_exec(snap, cfg, dag_states)
    assert not any((e.uuid == 'orphan-u' for e in orphans))
