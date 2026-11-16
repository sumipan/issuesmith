"""tests/observe/test_halt_consumer.py -- halt producer/consumer tests (AC-3, AC-4, AC-5)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from issuesmith.config import get_config
from issuesmith.observe.events import SystemicStepFailureEvent
from issuesmith.observe.policy import evaluate, execute
from issuesmith.queue_store import QueueStore

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
_NOW_ISO = _NOW.isoformat()

def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(queue_path=tmp_path / 'queue.jsonl', state_path=tmp_path / 'state.json', lock_path=tmp_path / 'lock')

def _enqueue(store: QueueStore, issue: int, phase: str='draft') -> None:
    store.enqueue(issue=issue, phase=phase, source='test', actor_kind='human', priority='normal', requested_by=['alice'], requested_at=_NOW_ISO)

def _fake_client(issues: dict | None=None) -> MagicMock:
    client = MagicMock()
    issues = issues or {}
    client.issue_get.side_effect = lambda n, **kwargs: issues.get(n, {'number': n, 'state': 'OPEN', 'labels': []})
    return client

class TestLastIssueHaltRuleRemoved:

    def test_dispatch_does_not_halt_when_last_issue_open_with_draft_done(self, tmp_path, monkeypatch):
        """AC-3: OPEN last_issue with draft-done label, 5min idle -- no halt fires."""
        from issuesmith import queue as qmod
        store = _store(tmp_path)
        _enqueue(store, 50, 'draft')
        client = _fake_client({40: {'state': 'OPEN', 'title': 'prev', 'body': _VALID_BODY, 'labels': [{'name': 'issuesmith:draft-done'}]}, 50: {'state': 'OPEN', 'title': 'next', 'body': _VALID_BODY, 'labels': []}})
        monkeypatch.setattr(qmod, '_pipeline_idle_enough_v2', lambda *a, **kw: True)
        monkeypatch.setattr(qmod, '_required_engines_paused', lambda *a, **kw: [])
        monkeypatch.setattr(qmod, '_dispatch_pipeline_ready', lambda *a, **kw: True)
        qmod.dispatch_one(now=_NOW, client=client, store=store, skip_seed=True, call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no llm')))
        snap = store.snapshot()
        assert snap.halt is False, f'halt should not be set, but reason: {snap.halt_reason}'

class TestHaltScopeAndEvent:

    def test_clear_halt_resets_scope_and_event(self, tmp_path):
        store = _store(tmp_path)
        store.set_halt(True, 'test', scope='phase:develop', event='systemic_step_failure')
        store.clear_halt()
        snap = store.snapshot()
        assert snap.halt is False
        assert snap.halt_scope == 'all'
        assert snap.halt_event is None

    def test_set_halt_default_scope_is_all(self, tmp_path):
        store = _store(tmp_path)
        store.set_halt(True, 'generic halt')
        snap = store.snapshot()
        assert snap.halt_scope == 'all'
        assert snap.halt_event == 'legacy'

class TestHaltResolutionViaObserve:

    def test_systemic_step_failure_produces_halt(self, tmp_path):
        store = _store(tmp_path)
        store.snapshot()
        event = SystemicStepFailureEvent(step='cp2', failure_class='ValueError', issues=(100, 101))
        cfg_obs = get_config().observe
        actions = evaluate([event], cfg_obs)
        execute(actions, store, sinks=[])
        snap2 = store.snapshot()
        assert snap2.halt is True
        assert snap2.halt_scope == 'phase:develop'
        assert snap2.halt_event == 'systemic_step_failure'
_VALID_BODY = '```yaml\ntarget_repo: sumipan/nexus\nbase_branch: main\nallow_paths:\n  - "src/**"\n```\n'
