"""tests for allow_paths conflict gate, strict_order, draft-done release, deps (#2980)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import ConcurrencyConfig, load_config, reset_config_cache
from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")


def _body(repo: str, *paths: str) -> str:
    lines = "\n".join(f'  - "{p}"' for p in paths)
    return (
        "```yaml\n"
        f"target_repo: {repo}\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f"{lines}\n"
        "```\n"
    )


_BODY_QUEUE = _body("sumipan/issuesmith", "tests/test_queue.py")
_BODY_TESTS_GLOB = _body("sumipan/issuesmith", "tests/**")
_BODY_SRC = _body("sumipan/issuesmith", "src/issuesmith/queue.py")
_BODY_SECRETARY = _body("sumipan/nexus", "tools/secretary/**")
_BODY_SKILLS = _body("sumipan/nexus", "skills/system-issuesmith/**")


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


def _write_engine_state(tmp_path: Path, design: str = "claude", implementation: str = "cursor") -> None:
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

    def issue_get(self, number, fields=None):
        data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        data.setdefault("state", "OPEN")
        data.setdefault("title", "t")
        data.setdefault("body", _BODY_QUEUE)
        data.setdefault("labels", [])
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
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


def test_allow_paths_conflict_overlapping_same_repo():
    from issuesmith.queue import _allow_paths_conflict

    in_flight = [
        {
            "issue": 2981,
            "engine": "claude",
            "role": "design",
            "target_repo": "sumipan/issuesmith",
            "allow_paths": ["tests/**"],
        }
    ]
    assert (
        _allow_paths_conflict("sumipan/issuesmith", ("tests/test_queue.py",), in_flight)
        == 2981
    )


def test_allow_paths_conflict_non_overlapping_same_repo():
    from issuesmith.queue import _allow_paths_conflict

    in_flight = [
        {
            "issue": 2976,
            "engine": "cursor",
            "role": "implementation",
            "target_repo": "sumipan/nexus",
            "allow_paths": ["tools/secretary/**"],
        }
    ]
    assert (
        _allow_paths_conflict(
            "sumipan/nexus", ("skills/system-issuesmith/**",), in_flight
        )
        is None
    )


def test_allow_paths_conflict_different_repos():
    from issuesmith.queue import _allow_paths_conflict

    in_flight = [
        {
            "issue": 2976,
            "engine": "cursor",
            "role": "implementation",
            "target_repo": "sumipan/nexus",
            "allow_paths": ["tools/secretary/**"],
        }
    ]
    assert (
        _allow_paths_conflict(
            "sumipan/issuesmith", ("src/issuesmith/**",), in_flight
        )
        is None
    )


def test_allow_paths_conflict_legacy_entry_is_conflict():
    from issuesmith.queue import _allow_paths_conflict

    # Real shape from logs/issuesmith-queue-state.json (2026-09-09)
    legacy = {
        "issue": 2980,
        "engine": "claude",
        "dispatched_at": "2026-09-09T18:39:28.292503+09:00",
    }
    assert _allow_paths_conflict("sumipan/issuesmith", ("src/issuesmith/queue.py",), [legacy]) == 2980


def test_dispatch_skips_overlapping_paths(tmp_path, monkeypatch, issuesmith_config):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        2981,
        "claude",
        role="design",
        allow_paths=("tests/**",),
        target_repo="sumipan/issuesmith",
    )
    store.enqueue(
        issue=2980,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {2980: {"state": "OPEN", "labels": [], "body": _BODY_QUEUE}}
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is False


def test_dispatch_allows_non_overlapping_or_other_repo(
    tmp_path, monkeypatch, issuesmith_config,
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        2938,
        "cursor",
        role="implementation",
        allow_paths=("skills/system-issuesmith/**",),
        target_repo="sumipan/nexus",
    )
    store.enqueue(
        issue=2976,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    store.enqueue(
        issue=2980,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    store.mark_triaged(store.snapshot().revision)
    client = _DispatchClient(
        {
            2976: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
            2980: {"state": "OPEN", "labels": [], "body": _BODY_SRC},
        }
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    first = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert first.dispatched is True
    assert first.issue in (2976, 2980)


@pytest.mark.parametrize("phase", ["draft", "sub", "merge"])
def test_phase_preconditions_block_unsatisfied_deps(phase, monkeypatch):
    from issuesmith import queue as qmod

    class _DepClient:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "title": "dep",
                "labels": [],
                "body": "",
            }

        def api_request(self, *a, **k):
            return []

        def pr_list(self, *a, **k):
            return []

    body = (
        _BODY_QUEUE
        + "\n## 依存（先行）\n\n"
        + "| # | Issue |\n"
        + "| --- | --- |\n"
        + "| 1 | #2999 |\n"
    )
    labels: list[dict] = []
    if phase == "sub":
        labels = [{"name": "issuesmith:draft-done"}]
    issue = {"state": "OPEN", "labels": labels, "body": body, "title": "t"}
    client = _DepClient()
    if phase == "merge":
        monkeypatch.setattr(qmod, "_find_open_prs_closing_issue", lambda *a, **k: [1])
    ok, why = qmod._phase_preconditions(phase, issue, client, 10)
    assert ok is False
    assert why == "dependencies not satisfied"


def test_draft_done_releases_normal_issue(tmp_path, monkeypatch, issuesmith_config):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(100, "claude", role="design", allow_paths=("src/**",), target_repo="sumipan/nexus")
    store.enqueue(
        issue=200,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            100: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": _BODY_SKILLS,
            },
            200: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
        }
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True
    issues = {e["issue"] for e in store.snapshot().in_flight}
    assert 100 not in issues
    assert 200 in issues


def test_draft_done_does_not_release_when_develop_ready(
    tmp_path, monkeypatch, issuesmith_config,
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(100, "claude", role="design", allow_paths=("src/**",), target_repo="sumipan/nexus")
    store.enqueue(
        issue=200,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            100: {
                "state": "OPEN",
                "labels": [
                    {"name": "issuesmith:draft-done"},
                    {"name": "issuesmith:develop-ready"},
                ],
                "body": _BODY_SKILLS,
            },
            200: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
        }
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    issues = {e["issue"] for e in store.snapshot().in_flight}
    assert 100 in issues


@pytest.mark.parametrize("next_label", ["issuesmith:sub-ready", "issuesmith:merge-ready"])
def test_draft_done_releases_stale_design_entry_for_non_develop_phase(next_label):
    """Only develop-ready/running keeps a draft-done design entry in_flight."""
    from issuesmith import queue as qmod

    client = _DispatchClient(
        {
            100: {
                "state": "OPEN",
                "labels": [
                    {"name": "issuesmith:draft-done"},
                    {"name": next_label},
                ],
            }
        }
    )
    entry = {"issue": 100, "engine": "claude", "role": "design"}
    assert qmod._in_flight_should_release(client, entry) is True


def test_strict_order_breaks_on_capacity_wait(tmp_path, monkeypatch, issuesmith_config):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {
        "default": 1,
        "per_engine": {"claude": 1, "cursor": 1},
        "strict_order": True,
    }
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    _write_engine_state(tmp_path, design="claude", implementation="cursor")
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        1,
        "claude",
        role="design",
        allow_paths=("unrelated/**",),
        target_repo="sumipan/nexus",
    )
    # Head is design (claude full) — with strict_order must not skip to develop (cursor free)
    store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    store.enqueue(
        issue=11,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    # Freeze triage order so the test asserts dispatch scanning, not LLM permute.
    snap = store.snapshot()
    store.mark_triaged(snap.revision)
    client = _DispatchClient(
        {
            1: {"state": "OPEN", "labels": [], "body": _BODY_SKILLS},
            10: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
            11: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": _BODY_SRC,
            },
        }
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is False


def test_strict_order_false_allows_skip(tmp_path, monkeypatch, issuesmith_config):
    payload = yaml.safe_load(issuesmith_config.read_text(encoding="utf-8"))
    payload["concurrency"] = {
        "default": 1,
        "per_engine": {"claude": 1, "cursor": 1},
        "strict_order": False,
    }
    issuesmith_config.write_text(yaml.safe_dump(payload), encoding="utf-8")
    reset_config_cache()
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    _write_engine_state(tmp_path, design="claude", implementation="cursor")
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        1,
        "claude",
        role="design",
        allow_paths=("unrelated/**",),
        target_repo="sumipan/nexus",
    )
    store.enqueue(
        issue=10,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    store.enqueue(
        issue=11,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    snap = store.snapshot()
    store.mark_triaged(snap.revision)
    client = _DispatchClient(
        {
            1: {"state": "OPEN", "labels": [], "body": _BODY_SKILLS},
            10: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
            11: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": _BODY_SRC,
            },
        }
    )
    now = datetime(2026, 9, 9, 12, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True
    assert result.issue == 11


def test_concurrency_config_strict_order_default():
    assert ConcurrencyConfig(default=1, per_engine={}).strict_order is False


def test_status_shows_conflict_waiting(tmp_path, monkeypatch, issuesmith_config, capsys):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        2966,
        "cursor",
        role="implementation",
        allow_paths=("tools/secretary/**",),
        target_repo="sumipan/nexus",
    )
    r = store.enqueue(
        issue=2976,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            2966: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
            2976: {"state": "OPEN", "labels": [], "body": _BODY_SECRETARY},
        }
    )
    monkeypatch.setattr(qmod, "get_forge", lambda repo=None: client)
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    qmod._cmd_status(args)
    out = capsys.readouterr().out
    assert "issue=#2976" in out
    assert "role=design" in out
    assert "waiting:" in out
    assert "#2966" in out
    assert "tools/secretary/**" in out
    assert r.request_id[:8] in out


def test_sub_done_milestone_parent_releases_in_flight(tmp_path, monkeypatch, issuesmith_config):
    """sub-done の milestone 親は in_flight を解放し、allow_paths が重なる子の develop を通す（2026-09-10 #2934）。"""
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        2934, "cursor", role="implementation",
        allow_paths=("tools/mltgnt_bridge/progress.py",), target_repo="sumipan/nexus",
    )
    store.enqueue(
        issue=2999, phase="develop", source="milestone-chain", actor_kind="automation",
        priority="normal", requested_by=["chain"], requested_at=_NOW,
    )
    child_body = (
        "```yaml\ntarget_repo: sumipan/nexus\nbase_branch: main\nallow_paths:\n"
        '  - "tools/mltgnt_bridge/progress.py"\n```\n'
    )
    client = _DispatchClient(
        {
            2934: {
                "state": "OPEN",
                "labels": [{"name": "scope:milestone"}, {"name": "issuesmith:sub-done"}],
                "body": _BODY_SKILLS,
            },
            2999: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": child_body,
            },
        }
    )
    now = datetime(2026, 9, 10, 1, 0, tzinfo=_JST)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True and result.issue == 2999
    assert 2934 not in {e["issue"] for e in store.snapshot().in_flight}


def test_sub_running_milestone_parent_stays_in_flight(tmp_path, monkeypatch, issuesmith_config):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(2934, "cursor", role="implementation", allow_paths=("x/**",), target_repo="sumipan/nexus")
    client = _DispatchClient(
        {2934: {"state": "OPEN", "labels": [{"name": "issuesmith:sub-running"}], "body": _BODY_SKILLS}}
    )
    assert qmod._in_flight_should_release(client, store.snapshot().in_flight[0]) is False
