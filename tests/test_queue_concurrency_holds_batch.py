"""Queue concurrency: held entries, multi-dispatch per tick and open PR occupancy."""

from __future__ import annotations

import argparse
from datetime import datetime

import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore, in_flight_by_engine
from tests import test_queue_concurrency as _base
from tests.test_queue_concurrency import _JST, _NOW, _DispatchClient, _patch_paths, _store, _write_engine_state

_clear_config_cache = _base._clear_config_cache
issuesmith_config = _base.issuesmith_config


# ---------------------------------------------------------------------------
# held in_flight entries (#4816)
# ---------------------------------------------------------------------------


def test_in_flight_by_engine_skips_held_entries():
    entries = [
        {"issue": 1, "engine": "claude"},
        {"issue": 2, "engine": "codex"},
        {"issue": 3, "engine": "claude", "held": {"by": "wf:3:p1:0", "at": _NOW}},
    ]
    assert in_flight_by_engine(entries) == {"claude": 1, "codex": 1}


def test_in_flight_by_engine_held_role_and_legacy_entries_count_zero():
    role_map = {"design": "claude", "implementation": "cursor"}
    held = {"by": "wf:9:p1:0", "at": _NOW}
    entries = [
        {"issue": 1, "engine": "codex", "role": "design"},
        {"issue": 2, "engine": "claude"},
        {"issue": 3, "engine": "codex", "role": "implementation", "held": held},
        {"issue": 4, "engine": "claude", "held": held},
    ]
    assert in_flight_by_engine(entries, role_engine_map=role_map) == {"claude": 2}
    assert in_flight_by_engine(entries) == {"codex": 1, "claude": 1}


def test_hold_in_flight_keeps_fields_and_records_held(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(
        10,
        "claude",
        role="implementation",
        phase="develop",
        allow_paths=("src/a/**",),
        target_repo="sumipan/issuesmith",
    )
    assert store.hold_in_flight(10, by="wf:10:p1:0") is True
    entry = store.snapshot().in_flight[0]
    assert entry["engine"] == "claude"
    assert entry["role"] == "implementation"
    assert entry["phase"] == "develop"
    assert entry["target_repo"] == "sumipan/issuesmith"
    assert entry["allow_paths"] == ["src/a/**"]
    assert entry["held"]["by"] == "wf:10:p1:0"
    assert datetime.fromisoformat(entry["held"]["at"]).tzinfo is not None


def test_hold_in_flight_missing_issue_returns_false_without_change(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(10, "claude")
    before = store.state_path.read_text(encoding="utf-8")
    assert store.hold_in_flight(99, by="wf:99:p1:0") is False
    assert store.state_path.read_text(encoding="utf-8") == before


def test_release_hold_removes_held_only_when_owner_matches(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(
        10,
        "claude",
        role="implementation",
        phase="develop",
        allow_paths=("src/a/**",),
        target_repo="sumipan/issuesmith",
    )
    assert store.hold_in_flight(10, by="wf:10:p1:0") is True
    assert store.release_hold(10, by="wf:10:p1:0") is True
    entry = store.snapshot().in_flight[0]
    assert "held" not in entry
    assert entry["engine"] == "claude"
    assert entry["role"] == "implementation"
    assert entry["phase"] == "develop"
    assert entry["target_repo"] == "sumipan/issuesmith"
    assert entry["allow_paths"] == ["src/a/**"]


def test_release_hold_wrong_owner_leaves_state_unchanged(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(10, "claude")
    assert store.hold_in_flight(10, by="wf:10:p1:0") is True
    before = store.state_path.read_text(encoding="utf-8")
    assert store.release_hold(10, by="wf:10:p1:1") is False
    assert store.state_path.read_text(encoding="utf-8") == before


def test_release_hold_no_entry_or_no_held_mark(tmp_path):
    store = _store(tmp_path)
    assert store.release_hold(99, by="wf:99:p1:0") is False
    store.add_in_flight(10, "claude")
    before_entry = store.state_path.read_text(encoding="utf-8")
    assert store.release_hold(10, by="wf:10:p1:0") is False
    assert store.state_path.read_text(encoding="utf-8") == before_entry


def test_release_hold_non_dict_held_is_noop(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(10, "claude")
    with store.lock():
        state = store._load_state_unlocked()
        state["in_flight"][0]["held"] = "bad"
        store._save_state_unlocked(state)
    before = store.state_path.read_text(encoding="utf-8")
    assert store.release_hold(10, by="wf:10:p1:0") is False
    assert store.state_path.read_text(encoding="utf-8") == before


def test_hold_in_flight_again_overwrites_held_only(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(10, "claude", role="implementation", phase="develop")
    store.add_in_flight(11, "claude")
    assert store.hold_in_flight(10, by="wf:10:p1:0") is True
    first = store.snapshot().in_flight
    assert store.hold_in_flight(10, by="wf:10:p1:1") is True
    second = store.snapshot().in_flight
    assert second[0]["held"]["by"] == "wf:10:p1:1"
    assert {k: v for k, v in second[0].items() if k != "held"} == {
        k: v for k, v in first[0].items() if k != "held"
    }
    assert second[1] == first[1]


def _held_dispatch_setup(tmp_path, monkeypatch, issuesmith_config, candidate_paths: str):
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
    assert store.hold_in_flight(300, by="wf:300:p1:0") is True
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
            300: {"state": "OPEN", "labels": [{"name": "issuesmith:andon-blocked"}]},
            301: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": candidate_body,
            },
        }
    )
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    return qmod, store, client


def test_dispatch_ignores_held_entry_for_engine_limit(tmp_path, monkeypatch, issuesmith_config):
    qmod, store, client = _held_dispatch_setup(
        tmp_path, monkeypatch, issuesmith_config, "tools/other/**"
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True
    issues = {entry["issue"]: entry for entry in store.snapshot().in_flight}
    assert set(issues) == {300, 301}
    assert "held" in issues[300]
    assert "held" not in issues[301]


def test_dispatch_held_entry_still_blocks_overlapping_paths(
    tmp_path, monkeypatch, issuesmith_config,
):
    qmod, store, client = _held_dispatch_setup(
        tmp_path, monkeypatch, issuesmith_config, "tools/held/x.py"
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is False
    assert [entry["issue"] for entry in store.snapshot().in_flight] == [300]


def test_status_shows_held_count_and_andon_id(tmp_path, monkeypatch, issuesmith_config, capsys):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 6, "codex": 1}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    for issue in (50, 51, 52, 53):
        store.add_in_flight(issue, "claude")
    store.hold_in_flight(53, by="wf:53:p1:0")
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    qmod._cmd_status(args)
    out = capsys.readouterr().out
    assert "in_flight: {claude: 3/6 (+1 held), codex: 0/1" in out
    assert "codex: 0/1 (+" not in out
    assert "- in_flight issue=#53 engine=claude held=andon:wf:53:p1:0" in out
    assert "- in_flight issue=#50 engine=claude\n" in out


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


def test_one_tick_starts_each_engine_up_to_its_limit(tmp_path, monkeypatch, issuesmith_config):
    qmod = _multi_tick_setup(
        tmp_path,
        monkeypatch,
        issuesmith_config,
        {"default": 1, "per_engine": {"claude": 2, "codex": 1}, "max_dispatch_per_tick": 6},
    )
    store = _store(tmp_path)
    _enqueue(store, 100, "develop")
    _enqueue(store, 101, "develop")
    _enqueue(store, 200, "draft")
    _enqueue(store, 201, "draft")
    _enqueue(store, 202, "draft")
    client = _DispatchClient(
        {
            100: {"labels": [{"name": "issuesmith:draft-done"}], "body": _body("tools/a/**")},
            101: {"labels": [{"name": "issuesmith:draft-done"}], "body": _body("tools/b/**")},
            200: {"body": _body("tools/c/**")},
            201: {"body": _body("tools/d/**")},
            202: {"body": _body("tools/e/**")},
        }
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    assert result.dispatched is True
    assert result.issue == result.started[0].issue
    by_engine: dict[str, list[int]] = {}
    for item in result.started:
        by_engine.setdefault(item.engine, []).append(item.issue)
    assert len(by_engine["codex"]) == 1 and set(by_engine["codex"]) <= {100, 101}
    assert len(by_engine["claude"]) == 2 and set(by_engine["claude"]) <= {200, 201, 202}
    snap = store.snapshot()
    assert sorted(e["issue"] for e in snap.in_flight) == sorted(s.issue for s in result.started)
    assert len(snap.active_order) == 2


def test_default_starts_one_request_per_tick(tmp_path, monkeypatch, issuesmith_config):
    qmod = _multi_tick_setup(
        tmp_path,
        monkeypatch,
        issuesmith_config,
        {"default": 1, "per_engine": {"claude": 2, "codex": 1}},
    )
    store = _store(tmp_path)
    _enqueue(store, 100, "develop")
    _enqueue(store, 200, "draft")
    client = _DispatchClient(
        {
            100: {"labels": [{"name": "issuesmith:draft-done"}], "body": _body("tools/a/**")},
            200: {"body": _body("tools/c/**")},
        }
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    assert len(result.started) == 1
    assert len(store.snapshot().in_flight) == 1


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


def test_dispatch_respects_open_pr_allow_paths_occupancy(
    tmp_path, monkeypatch, issuesmith_config,
):
    qmod = _multi_tick_setup(
        tmp_path,
        monkeypatch,
        issuesmith_config,
        {"default": 1, "per_engine": {"codex": 2}, "max_dispatch_per_tick": 6},
    )
    store = _store(tmp_path)
    _enqueue(store, 100, "develop")
    _enqueue(store, 200, "develop")
    shared = _body("tools/x.py")
    client = _OpenPrDispatchClient(
        {
            100: {"labels": [{"name": "issuesmith:draft-done"}], "body": shared},
            200: {"labels": [{"name": "issuesmith:draft-done"}], "body": shared},
        },
        [{"number": 55, "headRefName": "feat/issue-100-abc"}],
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    started = [s.issue for s in result.started]
    assert started == [100]
    snap = store.snapshot()
    remaining = [store.effective_request(snap, rid).issue for rid in snap.active_order]
    assert remaining == [200]
