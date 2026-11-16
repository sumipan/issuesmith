"""tests/observe/test_api_call_budget.py -- observe() forge API call budget (#3768)."""
from __future__ import annotations

import dataclasses
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from issuesmith.config import ObserveConfig, get_config
from issuesmith.observe import observe
from issuesmith.queue_store import QueueStore

_NOW = datetime(2026, 9, 25, 4, 0, 0, tzinfo=timezone.utc)

class CountingForge:
    """ForgePort stub that records every method call (any attribute is callable)."""

    def __init__(self, issues: dict[int, dict], running: list[dict] | None=None) -> None:
        self._issues = issues
        self._running = running or []
        self.calls: list[tuple[str, tuple, dict]] = []

    def issue_get(self, number: int, fields: list[str] | None=None) -> dict:
        self.calls.append(('issue_get', (number,), {'fields': fields}))
        return self._issues.get(number, {'number': number, 'state': 'OPEN', 'labels': []})

    def list_issues(self, label: str, state: str='open') -> list[dict]:
        self.calls.append(('list_issues', (label,), {'state': state}))
        return list(self._running)

    def __getattr__(self, name: str) -> Any:

        def _call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args, kwargs))
            return []
        return _call

    def issue_get_counts(self) -> Counter:
        return Counter((args[0] for name, args, _ in self.calls if name == 'issue_get'))

def _issue(num: int, *labels: str) -> dict:
    return {'number': num, 'state': 'OPEN', 'labels': [{'name': lbl} for lbl in labels]}

def _setup(tmp_path: Path, in_flight: list[int], failed: list[int]):
    store = QueueStore(queue_path=tmp_path / 'queue.jsonl', state_path=tmp_path / 'state.json', lock_path=tmp_path / 'lock')
    dispatched_at = (_NOW - timedelta(minutes=5)).isoformat()
    snap = dataclasses.replace(store.snapshot(), in_flight=[{'issue': n, 'engine': 'claude', 'role': 'implementation', 'dispatched_at': dispatched_at} for n in in_flight])
    exec_path = tmp_path / 'exec.jsonl'
    done_dir = tmp_path / 'done'
    done_dir.mkdir()
    rows = []
    for n in failed:
        uuid = f'u-p2-{n}'
        rows.append({'uuid': uuid, 'idempotency_key': f'issuesmith:impl:{n}', 'depends': [], 'annotations': {'step_name': 'p2'}})
        (done_dir / uuid).write_text('1', encoding='utf-8')
    exec_path.write_text(''.join((json.dumps(r) + '\n' for r in rows)), encoding='utf-8')
    cfg = get_config()
    cfg = dataclasses.replace(cfg, paths=dataclasses.replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir))
    return (snap, cfg)

def test_default_max_api_calls_is_8():
    assert ObserveConfig().max_api_calls == 8

def test_budget_exhausted_caps_calls_and_warns_once(tmp_path, caplog):
    """More issues than the budget -> calls capped at max_api_calls, one WARN line."""
    nums = list(range(101, 121))
    snap, cfg = _setup(tmp_path, in_flight=nums, failed=nums)
    client = CountingForge({})
    with caplog.at_level(logging.WARNING, logger='issuesmith.observe'):
        observe(snap, client, cfg, now=_NOW)
    assert len(client.calls) <= cfg.observe.max_api_calls
    assert max(client.issue_get_counts().values()) == 1
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and 'max_api_calls' in r.getMessage()]
    assert len(warns) == 1
