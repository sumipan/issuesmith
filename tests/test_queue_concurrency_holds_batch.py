"""Queue concurrency: stopped-issue slot release and multi-dispatch per tick."""

from __future__ import annotations

from datetime import datetime

import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore, in_flight_by_engine
from tests import test_queue_concurrency as _base
from tests.test_queue_concurrency import _JST, _NOW, _DispatchClient, _patch_paths, _store, _write_engine_state

_clear_config_cache = _base._clear_config_cache
issuesmith_config = _base.issuesmith_config


def test_in_flight_by_engine_counts_all_entries():
    entries = [
        {"issue": 1, "engine": "claude"},
        {"issue": 2, "engine": "codex"},
        {"issue": 3, "engine": "claude"},
    ]
    assert in_flight_by_engine(entries) == {"claude": 2, "codex": 1}


def test_drop_stopped_issue_missing_in_flight_still_records_stop(tmp_path):
    store = _store(tmp_path)
    store.enqueue(
        issue=99,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    store.drop_stopped_issue(99, "wf:99:p1:0")
    assert store.snapshot().stopped == [{"issue": 99, "andon": "wf:99:p1:0"}]


def _stopped_dispatch_setup(tmp_path, monkeypatch, issuesmith_config, candidate_paths: str):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 1}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    candidate_body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f'  - "{candidate_paths}"\n'
        "```\n"
    )
    store = _store(tmp_path)
    store.add_in_flight(
        300,
        "claude",
        role="implementation",
        phase="develop",
        allow_paths=("tools/held/**",),
        target_repo="sumipan/nexus",
    )
    store.drop_stopped_issue(300, "wf:300:p1:0")
    store.enqueue(
        issue=301,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            301: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": candidate_body,
            },
        }
    )
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    return qmod, store, client


def test_stopped_issue_frees_claude_slot_for_dispatch(tmp_path, monkeypatch, issuesmith_config):
    """refs #7464: a stopped issue no longer occupies an engine concurrency slot."""
    qmod, store, client = _stopped_dispatch_setup(
        tmp_path, monkeypatch, issuesmith_config, "tools/next/**"
    )
    result = qmod.dispatch_one(now=datetime(2026, 9, 5, 12, 0, tzinfo=_JST), client=client, store=store, skip_seed=True)
    assert result.dispatched and result.issue == 301


def _body(paths: str) -> str:
    return (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f'  - "{paths}"\n'
        "```\n"
    )


def _multi_tick_setup(tmp_path, monkeypatch, issuesmith_config, concurrency):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = concurrency
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    _write_engine_state(tmp_path, design="claude", implementation="codex")
    from issuesmith import queue as qmod

    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    return qmod


def _enqueue(store: QueueStore, issue: int, phase: str) -> None:
    store.enqueue(
        issue=issue,
        phase=phase,
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )


def test_max_dispatch_per_tick_parsed():
    from issuesmith.config import _build_concurrency

    assert _build_concurrency({"default": 2}).max_dispatch_per_tick == 1
    assert _build_concurrency({"max_dispatch_per_tick": 4}).max_dispatch_per_tick == 4
    assert _build_concurrency({"max_dispatch_per_tick": 0}).max_dispatch_per_tick == 1


def test_same_tick_dispatch_sees_allow_paths_conflict(tmp_path, monkeypatch, issuesmith_config):
    qmod = _multi_tick_setup(
        tmp_path,
        monkeypatch,
        issuesmith_config,
        {"default": 1, "per_engine": {"claude": 2, "codex": 3}, "max_dispatch_per_tick": 6},
    )
    store = _store(tmp_path)
    _enqueue(store, 100, "develop")
    _enqueue(store, 101, "develop")
    _enqueue(store, 102, "develop")
    client = _DispatchClient(
        {
            100: {"labels": [{"name": "issuesmith:draft-done"}], "body": _body("tools/a/**")},
            101: {"labels": [{"name": "issuesmith:draft-done"}], "body": _body("tools/a/**")},
            102: {"labels": [{"name": "issuesmith:draft-done"}], "body": _body("tools/b/**")},
        }
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    started = sorted(s.issue for s in result.started)
    assert len(started) == 2 and 102 in started  # 100 and 101 share tools/a/**
    snap = store.snapshot()
    remaining = [store.effective_request(snap, rid).issue for rid in snap.active_order]
    assert len(remaining) == 1 and remaining[0] in {100, 101}


class _OpenPrDispatchClient(_DispatchClient):
    def __init__(self, issues: dict[int, dict], prs: list[dict]):
        super().__init__(issues)
        self._prs = prs

    def pr_list(self, state="open", limit=100):
        return list(self._prs)

    def pr_get(self, number):
        for pr in self._prs:
            if pr.get("number") == number:
                return pr
        raise RuntimeError(f"pr {number} missing")
