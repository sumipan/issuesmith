"""tests for issuesmith queue concurrency, in-flight tracking, idle, and halt (#2867)."""

from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import ConcurrencyConfig, load_config, reset_config_cache
from issuesmith.queue_store import QueueStore, in_flight_by_engine

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def issuesmith_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    payload = {
        "repo": "sumipan/nexus",
        "label_namespace": "issuesmith",
        "timezone": "Asia/Tokyo",
        "supported_repos": ["sumipan/nexus"],
        "paths": {
            "queue": "jobs/issuesmith-queue.jsonl",
            "queue_state": "logs/issuesmith-queue-state.json",
            "queue_lock": "logs/issuesmith-queue.lock",
            "triage_log": "jobs/issuesmith-triage.jsonl",
            "seed": "configs/night-queue.yaml",
            "night_state": "logs/night-queue-state.json",
            "exec_jsonl": "jobs/exec.jsonl",
            "done_dir": "jobs/done",
            "quota_state": "jobs/quota-gate.json",
            "metrics": "jobs/metrics.jsonl",
            "worktrees_dir": ".claude/worktrees",
            "external_dir": ".claude/external",
            "workflow": "workflows/issuesmith.yml",
            "template_dir": "workflows/issuesmith",
            "engine_state": ".pipeline-state/issuesmith-engine.yml",
        },
        "engines": {
            "design": {
                "allowed": ["claude", "codex"],
                "default_model": {"claude": "claude-sonnet-4-6"},
                "timeout_sec": 1800,
            },
            "implementation": {
                "allowed": ["claude", "cursor"],
                "default_model": {"claude": "claude-sonnet-4-6", "cursor": "auto"},
                "timeout_sec": 3600,
            },
        },
        "concurrency": {"default": 1},
    }
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield cfg_path
    reset_config_cache()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _write_engine_state(tmp_path: Path, design: str = "claude", implementation: str = "claude") -> None:
    path = tmp_path / ".pipeline-state" / "issuesmith-engine.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {
                "design": {"engine": design},
                "implementation": {"engine": implementation},
            }
        ),
        encoding="utf-8",
    )


def _patch_paths(tmp_path: Path, monkeypatch, issuesmith_config):
    from issuesmith import config as cfgmod
    from issuesmith import queue as qmod
    from issuesmith import queue_store as qstore

    cfg = load_config(issuesmith_config)
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    exec_path.parent.mkdir(parents=True, exist_ok=True)
    exec_path.write_text("", encoding="utf-8")
    engine_state = tmp_path / ".pipeline-state" / "issuesmith-engine.yml"
    _write_engine_state(tmp_path)

    new_paths = cfgmod.PathsConfig(
        queue=tmp_path / "queue.jsonl",
        queue_state=tmp_path / "state.json",
        queue_lock=tmp_path / "lock",
        triage_log=tmp_path / "triage.jsonl",
        seed=tmp_path / "seed.yaml",
        night_state=tmp_path / "night.json",
        exec_jsonl=exec_path,
        done_dir=done_dir,
        quota_state=tmp_path / "quota.json",
        metrics=tmp_path / "metrics.jsonl",
        worktrees_dir=tmp_path / "wt",
        external_dir=tmp_path / "ext",
        workflow=tmp_path / "wf.yml",
        template_dir=tmp_path / "templates",
        engine_state=engine_state,
    )
    patched_cfg = cfgmod.IssuesmithConfig(
        repo=cfg.repo,
        label_namespace=cfg.label_namespace,
        timezone=cfg.timezone,
        supported_repos=cfg.supported_repos,
        root=tmp_path,
        paths=new_paths,
        engines=cfg.engines,
        concurrency=cfg.concurrency,
    )
    monkeypatch.setattr(cfgmod, "get_config", lambda: patched_cfg)
    monkeypatch.setattr(qmod, "_cfg", patched_cfg)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_state)
    monkeypatch.setattr(qstore, "_cfg", patched_cfg)


class _DispatchClient:
    def __init__(self, issues: dict[int, dict] | None = None):
        self.issues = issues or {}
        self.updates: list[tuple[int, list[str] | None]] = []
        self.comments: list[tuple[int, str]] = []
        self._lock = threading.Lock()

    def issue_get(self, number, fields=None):
        with self._lock:
            data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        data.setdefault("state", "OPEN")
        data.setdefault("title", "t")
        data.setdefault("body", _VALID_BODY)
        data.setdefault("labels", [])
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
        with self._lock:
            self.updates.append((number, labels_add))
            bucket = self.issues.setdefault(number, {"labels": []})
            names = {lab["name"] if isinstance(lab, dict) else lab for lab in bucket.get("labels", [])}
            for lab in labels_add or []:
                names.add(lab)
            for lab in labels_remove or []:
                names.discard(lab)
            bucket["labels"] = [{"name": n} for n in sorted(names)]

    def issue_comment(self, number, body):
        self.comments.append((number, body))

    def get_issue_comments(self, number):
        return []


def test_concurrency_default_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "issuesmith.config._package_fallback_yaml",
        lambda: tmp_path / "missing-issuesmith.yaml",
    )
    cfg = load_config()
    assert cfg.concurrency == ConcurrencyConfig(default=1, per_engine={})
    assert cfg.concurrency.limit("claude") == 1


def test_in_flight_by_engine_counts():
    entries = [
        {"issue": 1, "engine": "claude"},
        {"issue": 2, "engine": "claude"},
        {"issue": 3, "engine": "codex"},
    ]
    assert in_flight_by_engine(entries) == {"claude": 2, "codex": 1}


def test_store_add_and_remove_in_flight(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(10, "claude")
    snap = store.snapshot()
    assert len(snap.in_flight) == 1
    assert snap.in_flight[0]["issue"] == 10
    assert snap.in_flight[0]["engine"] == "claude"
    store.remove_in_flight(10)
    assert store.snapshot().in_flight == []


def test_claude_limit_two_allows_two_dispatches(
    tmp_path, monkeypatch, issuesmith_config,
):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 2}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    body_a = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/a/**"\n'
        "```\n"
    )
    body_b = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/b/**"\n'
        "```\n"
    )
    body_c = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/c/**"\n'
        "```\n"
    )

    store = _store(tmp_path)
    for issue in (100, 101, 102):
        store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
    client = _DispatchClient(
        {
            100: {"state": "OPEN", "labels": [], "body": body_a},
            101: {"state": "OPEN", "labels": [], "body": body_b},
            102: {"state": "OPEN", "labels": [], "body": body_c},
        }
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])

    first = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    second = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    third = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    assert first.dispatched is True
    assert second.dispatched is True
    assert third.dispatched is False
    assert len(store.snapshot().in_flight) == 2


def test_different_engines_do_not_share_limits(
    tmp_path, monkeypatch, issuesmith_config,
):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 1, "codex": 1}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    _write_engine_state(tmp_path, design="claude", implementation="codex")

    from issuesmith import queue as qmod

    draft_body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/draft/**"\n'
        "```\n"
    )
    develop_body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/develop/**"\n'
        "```\n"
    )

    store = _store(tmp_path)
    store.enqueue(
        issue=100,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    store.enqueue(
        issue=101,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            100: {"state": "OPEN", "labels": [], "body": draft_body},
            101: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": develop_body,
            },
        }
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])

    first = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    second = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    assert first.dispatched is True
    assert second.dispatched is True
    snap = store.snapshot()
    engines = {entry["engine"] for entry in snap.in_flight}
    assert engines == {"claude", "codex"}


def test_in_flight_role_remap_codex_to_claude():
    """#2969: design engine switched codex→claude; counts follow current role map."""
    entries = [
        {
            "issue": 2969,
            "engine": "codex",
            "role": "design",
            "dispatched_at": "2026-09-09T12:00:00+09:00",
        }
    ]
    assert in_flight_by_engine(entries) == {"codex": 1}
    assert in_flight_by_engine(
        entries, role_engine_map={"design": "claude", "implementation": "cursor"}
    ) == {"claude": 1}


def test_in_flight_legacy_entry_counts_stored_engine():
    # Real shape from nexus logs/issuesmith-queue-state.json (2026-09-09)
    legacy = {
        "issue": 2980,
        "engine": "claude",
        "dispatched_at": "2026-09-09T18:39:28.292503+09:00",
    }
    assert in_flight_by_engine([legacy]) == {"claude": 1}
    assert in_flight_by_engine(
        [legacy], role_engine_map={"design": "codex", "implementation": "cursor"}
    ) == {"claude": 1}


def test_add_in_flight_stores_role_and_paths(tmp_path):
    store = _store(tmp_path)
    store.add_in_flight(
        10,
        "codex",
        role="design",
        allow_paths=("tests/**",),
        target_repo="sumipan/issuesmith",
    )
    entry = store.snapshot().in_flight[0]
    assert entry["role"] == "design"
    assert entry["allow_paths"] == ["tests/**"]
    assert entry["target_repo"] == "sumipan/issuesmith"
    assert in_flight_by_engine(
        [entry], role_engine_map={"design": "claude", "implementation": "cursor"}
    ) == {"claude": 1}


def test_one_tick_dispatches_at_most_one(tmp_path, monkeypatch, issuesmith_config):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 3}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    for issue in (200, 201, 202):
        store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
    client = _DispatchClient({i: {"state": "OPEN", "labels": []} for i in (200, 201, 202)})
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True
    assert len(store.snapshot().in_flight) == 1


def test_status_shows_in_flight_line(tmp_path, monkeypatch, issuesmith_config, capsys):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {"default": 1, "per_engine": {"claude": 2, "codex": 1}}
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(50, "claude")
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    qmod._cmd_status(args)
    out = capsys.readouterr().out
    assert "in_flight: {claude: 1/2, codex: 0/1" in out


def test_parallel_dispatch_respects_limit(tmp_path, monkeypatch, issuesmith_config):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(None)
    for issue in (50, 51):
        store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )
    client = _DispatchClient(
        {50: {"state": "OPEN", "labels": []}, 51: {"state": "OPEN", "labels": []}}
    )
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    barrier = threading.Barrier(2)
    results: list = []

    def worker():
        barrier.wait()
        results.append(
            qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
        )

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    wins = [r for r in results if r.dispatched]
    assert len(wins) == 1


def test_store_in_flight_blocks_when_labels_not_reflected(
    tmp_path, monkeypatch, issuesmith_config,
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(99, "claude")
    store.enqueue(
        issue=100,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient({100: {"state": "OPEN", "labels": []}})
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is False


def test_pipeline_idle_v2_false_when_in_flight(tmp_path, monkeypatch, issuesmith_config):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(50, "claude")
    snap = store.snapshot()
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    assert qmod._pipeline_idle_enough_v2(snap, idle_minutes=5, now=now) is False


def test_non_issuesmith_done_does_not_affect_idle(
    tmp_path, monkeypatch, issuesmith_config,
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    done_dir = tmp_path / "jobs" / "done"
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    issuesmith_uuid = "11111111-1111-4111-8111-111111111111"
    exec_path.write_text(
        json.dumps(
            {
                "uuid": issuesmith_uuid,
                "idempotency_key": "issuesmith:draft:100",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (done_dir / issuesmith_uuid).write_text("0\n", encoding="utf-8")
    (done_dir / "other-task-uuid").write_text("0\n", encoding="utf-8")

    old_mtime = datetime(2026, 9, 5, 11, 0, tzinfo=_JST).timestamp()
    new_mtime = datetime(2026, 9, 5, 11, 30, tzinfo=_JST).timestamp()
    os.utime(done_dir / issuesmith_uuid, (old_mtime, old_mtime))
    os.utime(done_dir / "other-task-uuid", (new_mtime, new_mtime))

    snap = _store(tmp_path).snapshot()
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    assert qmod._pipeline_idle_enough_v2(snap, idle_minutes=5, now=now) is True


def test_halt_auto_clears_when_in_flight_resolved(
    tmp_path, monkeypatch, issuesmith_config,
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_halt(
        True,
        "last_issue #2852 is still OPEN while pipeline idle for >= 30 minutes",
    )
    store.enqueue(
        issue=300,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient({300: {"state": "OPEN", "labels": []}})
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True
    assert store.snapshot().halt is False


def test_dispatch_bumps_ghdag_generation_when_handler_key_consumed(
    tmp_path, monkeypatch, issuesmith_config,
):
    """ready ラベル付与時に冪等キーが消費済みなら ghdag redispatch を呼ぶ（2026-09-09 #2980）。"""
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/a/**"\n'
        "```\n"
    )
    store = _store(tmp_path)
    store.enqueue(
        issue=100,
        phase="draft",
        source="recovery",
        actor_kind="human",
        priority="high",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient({100: {"state": "OPEN", "labels": [], "body": body}})
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    consumed_queries: list[tuple[str, int]] = []
    triggered: list[tuple[int, str]] = []
    monkeypatch.setattr(
        qmod, "_handler_key_consumed",
        lambda handler, issue: consumed_queries.append((handler, issue)) or True,
    )
    monkeypatch.setattr(
        qmod, "_trigger_ghdag_redispatch",
        lambda issue, handler, reason: triggered.append((issue, handler)) or 0,
    )

    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)

    assert result.dispatched is True
    assert consumed_queries == [("brushup", 100)]
    assert triggered == [(100, "brushup")]


def test_dispatch_skips_ghdag_redispatch_when_key_unused(
    tmp_path, monkeypatch, issuesmith_config,
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)

    from issuesmith import queue as qmod

    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/a/**"\n'
        "```\n"
    )
    store = _store(tmp_path)
    store.enqueue(
        issue=100, phase="draft", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )
    client = _DispatchClient({100: {"state": "OPEN", "labels": [], "body": body}})
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    monkeypatch.setattr(qmod, "_handler_key_consumed", lambda handler, issue: False)
    monkeypatch.setattr(
        qmod, "_trigger_ghdag_redispatch",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    assert qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True).dispatched
