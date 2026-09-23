"""execute() raises each observe andon once per occurrence (sumipan/nexus#3621)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import answer, list_open
from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe.events import ChainHaltedEvent, ForgeUnavailableEvent, IssueStallEvent
from issuesmith.observe.policy import AndonAction, evaluate, execute
from issuesmith.queue_store import QueueStore


class RecordingSink:
    def __init__(self) -> None:
        self.emitted: list = []

    def emit(self, andon) -> None:
        self.emitted.append(andon)


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> QueueStore:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))  # metrics / jobs paths resolve under tmp
    reset_config_cache()
    yield QueueStore(queue_path=tmp_path / "q.jsonl", state_path=tmp_path / "state.json", lock_path=tmp_path / "q.lock")
    reset_config_cache()


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    return get_forge()


def _halted(parent: int) -> list:
    return evaluate([ChainHaltedEvent(parent=parent, reason="validation failed")], get_config().observe)


def test_andon_id_is_stable_across_changing_summary():
    first = evaluate([IssueStallEvent(issue=7, phase="develop-running", minutes=80)], get_config().observe)[0]
    later = evaluate([IssueStallEvent(issue=7, phase="develop-running", minutes=95)], get_config().observe)[0]
    assert isinstance(first, AndonAction) and first.summary != later.summary
    assert first.andon_id == later.andon_id == "observe:7:stall:develop-running:0"


def test_same_condition_three_ticks_raises_once(store, client):
    number = client.issue_create("milestone", "body")
    sink = RecordingSink()

    for _ in range(3):
        execute(_halted(number), store, sinks=[sink], client=client)

    assert len(sink.emitted) == 1
    comments = client.get_issue_comments(number)
    assert len(comments) == 1
    assert [a.id for a in list_open(client)] == [f"observe:{number}:chain_halted:0"]


def test_answered_andon_is_not_raised_again_while_condition_persists(store, client):
    number = client.issue_create("milestone", "body")
    sink = RecordingSink()
    execute(_halted(number), store, sinks=[sink], client=client)
    answer(client, f"observe:{number}:chain_halted:0", "accept")
    assert list_open(client) == []

    execute(_halted(number), store, sinks=[sink], client=client)

    assert len(sink.emitted) == 1
    assert list_open(client) == []


def test_condition_cleared_then_recurring_is_raised_again(store, client):
    number = client.issue_create("milestone", "body")
    sink = RecordingSink()
    execute(_halted(number), store, sinks=[sink], client=client)
    execute([], store, sinks=[sink], client=client)  # condition gone
    execute(_halted(number), store, sinks=[sink], client=client)  # recurs

    assert len(sink.emitted) == 2
    assert len(client.get_issue_comments(number)) == 2


def test_issue_less_andon_is_emitted_once_to_sinks(store, client):
    sink = RecordingSink()
    actions = evaluate([ForgeUnavailableEvent(consecutive=3)], get_config().observe)
    andons = [a for a in actions if isinstance(a, AndonAction)]
    assert andons and andons[0].issue == 0

    execute(andons, store, sinks=[sink], client=client)
    execute(andons, store, sinks=[sink], client=client)

    assert len(sink.emitted) == 1
    assert sink.emitted[0].id == "observe:0:forge_unavailable:0"


def test_without_client_sink_only_but_still_once(store):
    sink = RecordingSink()
    execute(_halted(42), store, sinks=[sink])
    execute(_halted(42), store, sinks=[sink])
    assert [a.id for a in sink.emitted] == ["observe:42:chain_halted:0"]


def test_store_forgets_ids_that_are_no_longer_active(store):
    assert store.sync_observe_andons({"a", "b"}) == {"a", "b"}
    assert store.sync_observe_andons({"a"}) == set()
    assert store.sync_observe_andons({"a", "b"}) == {"b"}
