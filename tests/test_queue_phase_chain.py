"""Queue-owned phase chaining after in_flight phase completion (#5746)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import load_config, reset_config_cache
from issuesmith.queue_store import QueueStore
from tests.conftest import NEXUS_TEST_PHASES

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")

_MIGRATE_PHASE = {
    "name": "migrate",
    "role": "implementation",
    "entry_step": "mg1",
    "handler": "migrate",
    "steps": ["mg1"],
    "writes_files": False,
    "preconditions": ["scope:migration"],
    "advance_when": ["deps_terminal"],
}

_CHAIN_PHASES = []
for ph in NEXUS_TEST_PHASES:
    item = dict(ph)
    if item["name"] == "develop":
        item["advance_when"] = ["deps_terminal", "pins_landed"]
    _CHAIN_PHASES.append(item)
    if item["name"] == "develop":
        _CHAIN_PHASES.append(_MIGRATE_PHASE)


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
        "supported_repos": ["sumipan/nexus", "sumipan/issuesmith"],
        "phases": _CHAIN_PHASES,
        "terminal_labels": ["issuesmith:merge-done", "bump:done"],
        "terminal_without_merge": [
            "rejected",
            "superseded",
            "sub-ready",
            "sub-done",
        ],
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
        "concurrency": {"default": 1, "per_engine": {"claude": 2, "cursor": 2, "codex": 2}},
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
    engine_state.parent.mkdir(parents=True, exist_ok=True)
    engine_state.write_text(
        yaml.safe_dump(
            {"design": {"engine": "claude"}, "implementation": {"engine": "cursor"}}
        ),
        encoding="utf-8",
    )
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
        template_dir=tmp_path / "tpl",
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
        phases=cfg.phases,
        terminal_labels=cfg.terminal_labels,
        terminal_without_merge=cfg.terminal_without_merge,
    )
    monkeypatch.setattr(cfgmod, "get_config", lambda: patched_cfg)
    monkeypatch.setattr(qmod, "_cfg", patched_cfg)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qstore, "_cfg", patched_cfg)


class _Client:
    def __init__(self, issues: dict[int, dict]):
        self.issues = issues

    def issue_get(self, number, fields=None):
        row = dict(self.issues[number])
        if fields:
            return {k: row.get(k) for k in fields if k in row or k == "number"}
        return row


class _FailOnceClient(_Client):
    def __init__(self, issues: dict[int, dict]):
        super().__init__(issues)
        self.calls = 0

    def issue_get(self, number, fields=None):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary fetch failure")
        return super().issue_get(number, fields=fields)


def _body() -> str:
    return (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "```\n"
    )


class TestResolveNextPhase:
    def test_normal_issue_skips_sub_and_migrate(self, issuesmith_config):
        from issuesmith import queue as qmod

        labels = {"issuesmith:draft-done"}
        assert qmod._resolve_applicable_next_phase("draft", labels) == "develop"
        assert qmod._resolve_applicable_next_phase("develop", labels) == "merge"
        assert qmod._resolve_applicable_next_phase("merge", labels) is None

    def test_migration_issue_includes_migrate(self, issuesmith_config):
        from issuesmith import queue as qmod

        labels = {"issuesmith:draft-done", "scope:migration"}
        assert qmod._resolve_applicable_next_phase("develop", labels) == "migrate"
        assert qmod._resolve_applicable_next_phase("migrate", labels) == "merge"

    def test_milestone_parent_skips_develop(self, issuesmith_config):
        from issuesmith import queue as qmod

        labels = {"issuesmith:draft-done", "scope:milestone"}
        assert qmod._resolve_applicable_next_phase("draft", labels) == "sub"


class TestQueueChainEnqueue:
    def test_draft_done_enqueues_develop_with_chain_metadata(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        draft = store.enqueue(
            issue=42,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="high",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        store.complete(draft.request_id, "dispatched")
        store.add_in_flight(42, "claude", role="design", phase="draft")
        client = _Client(
            {
                42: {
                    "state": "OPEN",
                    "labels": [{"name": "issuesmith:draft-done"}],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        snap = store.snapshot()
        develop = [
            req
            for req in snap.requests.values()
            if req.issue == 42 and req.phase == "develop"
        ]
        assert len(develop) == 1
        assert develop[0].source == "queue-chain"
        assert develop[0].priority == "high"
        assert develop[0].requested_by == (f"queue-chain:{draft.request_id}",)
        assert not snap.in_flight

    def test_idempotent_on_repeated_tick(self, tmp_path, monkeypatch, issuesmith_config):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.complete(
            store.enqueue(
                issue=42,
                phase="draft",
                source="skill",
                actor_kind="human",
                priority="high",
                requested_by=["alice"],
                requested_at=_NOW,
            ).request_id,
            "dispatched",
        )
        store.add_in_flight(42, "claude", role="design", phase="draft")
        client = _Client(
            {
                42: {
                    "state": "OPEN",
                    "labels": [{"name": "issuesmith:draft-done"}],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        qmod._release_finished_in_flight(store, client, [])
        develop = [
            req
            for req in store.snapshot().requests.values()
            if req.issue == 42 and req.phase == "develop"
        ]
        assert len(develop) == 1

    def test_fetch_failure_keeps_design_slot_until_successor_is_enqueued(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.complete(
            store.enqueue(
                issue=42,
                phase="draft",
                source="skill",
                actor_kind="human",
                priority="high",
                requested_by=["alice"],
                requested_at=_NOW,
            ).request_id,
            "dispatched",
        )
        store.add_in_flight(42, "claude", role="design", phase="draft")
        client = _FailOnceClient(
            {
                42: {
                    "state": "OPEN",
                    "labels": [{"name": "issuesmith:draft-done"}],
                    "body": _body(),
                }
            }
        )

        qmod._release_finished_in_flight(store, client, [])
        snap = store.snapshot()
        assert snap.in_flight
        assert not [req for req in snap.requests.values() if req.phase == "develop"]

        qmod._release_finished_in_flight(store, client, [])
        snap = store.snapshot()
        assert not snap.in_flight
        assert len([req for req in snap.requests.values() if req.phase == "develop"]) == 1

    def test_skips_when_develop_already_ready(self, tmp_path, monkeypatch, issuesmith_config):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.add_in_flight(42, "claude", role="design", phase="draft")
        client = _Client(
            {
                42: {
                    "state": "OPEN",
                    "labels": [
                        {"name": "issuesmith:draft-done"},
                        {"name": "issuesmith:develop-ready"},
                    ],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        develop = [
            req
            for req in store.snapshot().requests.values()
            if req.issue == 42 and req.phase == "develop"
        ]
        assert develop == []

    def test_milestone_parent_does_not_chain(self, tmp_path, monkeypatch, issuesmith_config):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.add_in_flight(100, "claude", role="design", phase="draft")
        client = _Client(
            {
                100: {
                    "state": "OPEN",
                    "labels": [
                        {"name": "scope:milestone"},
                        {"name": "issuesmith:draft-done"},
                    ],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        chained = [
            req
            for req in store.snapshot().requests.values()
            if req.source == "queue-chain"
        ]
        assert chained == []

    def test_excluded_impl_label_does_not_chain(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        """Primary impl phase ``excludes`` blocks queue-chain after design completes."""
        from issuesmith import queue as qmod

        cfg_path = issuesmith_config
        payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        phases = []
        for ph in payload["phases"]:
            item = dict(ph)
            if item["name"] == "develop":
                item["excludes"] = ["scope:milestone", "scope:epic"]
            phases.append(item)
        payload["phases"] = phases
        cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        reset_config_cache()
        _patch_paths(tmp_path, monkeypatch, cfg_path)

        store = _store(tmp_path)
        store.add_in_flight(200, "claude", role="design", phase="draft")
        client = _Client(
            {
                200: {
                    "state": "OPEN",
                    "labels": [
                        {"name": "scope:epic"},
                        {"name": "issuesmith:draft-done"},
                    ],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        chained = [
            req
            for req in store.snapshot().requests.values()
            if req.source == "queue-chain"
        ]
        assert chained == []
        reset_config_cache()

    def test_terminal_issue_does_not_chain(self, tmp_path, monkeypatch, issuesmith_config):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.add_in_flight(42, "cursor", role="implementation", phase="develop")
        client = _Client(
            {
                42: {
                    "state": "OPEN",
                    "labels": [
                        {"name": "issuesmith:draft-done"},
                        {"name": "issuesmith:develop-done"},
                        {"name": "issuesmith:merge-done"},
                    ],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        assert not [
            req
            for req in store.snapshot().requests.values()
            if req.source == "queue-chain"
        ]

    def test_develop_done_chains_merge_keeps_impl_in_flight(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.add_in_flight(42, "cursor", role="implementation", phase="develop")
        client = _Client(
            {
                42: {
                    "state": "OPEN",
                    "labels": [
                        {"name": "issuesmith:draft-done"},
                        {"name": "issuesmith:develop-done"},
                    ],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        snap = store.snapshot()
        merge = [req for req in snap.requests.values() if req.phase == "merge"]
        assert len(merge) == 1
        assert merge[0].source == "queue-chain"
        assert {e["issue"] for e in snap.in_flight} == {42}

    def test_chain_waits_on_advance_when_via_plan(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.complete(
            store.enqueue(
                issue=42,
                phase="draft",
                source="skill",
                actor_kind="human",
                priority="normal",
                requested_by=["alice"],
                requested_at=_NOW,
            ).request_id,
            "dispatched",
        )
        store.add_in_flight(42, "claude", role="design", phase="draft")
        client = _Client(
            {
                42: {
                    "state": "OPEN",
                    "labels": [{"name": "issuesmith:draft-done"}],
                    "body": _body(),
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        now = datetime(2026, 10, 28, 12, 0, tzinfo=_JST)
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, n: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        monkeypatch.setattr(
            "issuesmith.pins.develop_pins_landed",
            lambda body, config: (False, "dependencies not satisfied: pin test not landed"),
        )
        queue_plan = qmod.plan(store, client, now=now)
        snap = store.snapshot()
        develop_entries = [
            e
            for e in queue_plan.entries
            if (snap.requests.get(e.request_id) is not None)
            and snap.requests[e.request_id].phase == "develop"
        ]
        assert len(develop_entries) == 1
        assert develop_entries[0].decision == "wait"
        assert develop_entries[0].reason != "ok"


class TestTerminalLabelsUseNamespace:
    def test_rejected_and_superseded_use_label_namespace(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        from issuesmith import queue as qmod

        cfg_path = issuesmith_config
        payload = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        payload["label_namespace"] = "acme"
        cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        reset_config_cache()
        _patch_paths(tmp_path, monkeypatch, cfg_path)

        store = _store(tmp_path)
        req = store.enqueue(
            issue=7,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )

        class _TrackingClient(_Client):
            def __init__(self):
                super().__init__({7: {"state": "OPEN", "labels": []}})
                self.updates: list[tuple] = []

            def issue_update(self, number, labels_add=None, labels_remove=None):
                self.updates.append((number, labels_add, labels_remove))

        client = _TrackingClient()
        qmod._apply_terminal(
            store,
            client,
            req.request_id,
            7,
            "superseded",
            "newer semver",
            add_rejected_label=True,
            comment=False,
        )
        assert "acme:rejected" in (client.updates[0][1] or [])
        assert "acme:superseded" in (client.updates[1][1] or [])
        reset_config_cache()


class TestDispatchIntegration:
    def test_release_path_chains_before_design_slot_freed(
        self, tmp_path, monkeypatch, issuesmith_config
    ):
        """Same as enqueue test; documents dispatch_one calls release before apply."""
        _patch_paths(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        draft = store.enqueue(
            issue=50,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="high",
            requested_by=["alice"],
            requested_at=_NOW,
        )
        store.complete(draft.request_id, "dispatched")
        store.add_in_flight(50, "claude", role="design", phase="draft")
        client = _Client(
            {
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _body(),
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            }
        )
        qmod._release_finished_in_flight(store, client, [])
        snap = store.snapshot()
        develop = [
            req
            for req in snap.requests.values()
            if req.issue == 50 and req.phase == "develop" and req.source == "queue-chain"
        ]
        assert len(develop) == 1
        assert develop[0].priority == "high"
        assert not snap.in_flight
