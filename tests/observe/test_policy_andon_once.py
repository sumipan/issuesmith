"""execute() raises each observe andon once per occurrence (sumipan/nexus#3621)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe.events import ChainHaltedEvent, ForgeUnavailableEvent
from issuesmith.observe.policy import AndonAction, evaluate, execute
from issuesmith.queue_store import QueueStore


class RecordingSink:

    def __init__(self) -> None:
        self.emitted: list = []

    def emit(self, andon) -> None:
        self.emitted.append(andon)

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

def _halted(parent: int) -> list:
    return evaluate([ChainHaltedEvent(parent=parent, reason='validation failed')], get_config().observe)

def test_andon_id_is_stable_across_changing_summary():
    cfg = get_config().observe
    first = evaluate([ForgeUnavailableEvent(consecutive=3)], cfg)[1]
    later = evaluate([ForgeUnavailableEvent(consecutive=7)], cfg)[1]
    assert isinstance(first, AndonAction) and first.summary != later.summary
    assert first.andon_id == later.andon_id == 'observe:0:forge_unavailable:0'

def test_condition_cleared_then_recurring_is_raised_again(store, client):
    number = client.issue_create('milestone', 'body')
    sink = RecordingSink()
    execute(_halted(number), store, sinks=[sink], client=client)
    execute([], store, sinks=[sink], client=client)
    execute(_halted(number), store, sinks=[sink], client=client)
    assert len(sink.emitted) == 2
    assert len(client.get_issue_comments(number)) == 3

def test_without_client_sink_only_but_still_once(store):
    sink = RecordingSink()
    execute(_halted(42), store, sinks=[sink])
    execute(_halted(42), store, sinks=[sink])
    assert [a.id for a in sink.emitted] == ['observe:42:chain_halted:0']
