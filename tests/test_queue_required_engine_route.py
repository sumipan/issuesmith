"""Queue dispatch engine selection follows config heavy routes (#7837)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue import apply, plan
from tests.test_queue import _NOW, _VALID_BODY, _ProdShapeClient, _store

_JST = ZoneInfo("Asia/Tokyo")
_FIXED_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=_JST)


def _write_quota(path: Path, engines: dict[str, str]) -> None:
    observed_at = "2026-09-10T09:19:19.749925+00:00"
    engine_payloads: dict[str, dict] = {}
    for name, st in engines.items():
        payload: dict = {
            "status": st,
            "observed_at": observed_at,
            "reason": "t",
            "resume_at": None,
        }
        if st == "paused":
            payload["sources"] = [
                {
                    "kind": "budget",
                    "reason": "t",
                    "until": None,
                    "set_by": "t",
                    "observed_at": observed_at,
                    "available": False,
                }
            ]
        engine_payloads[name] = payload
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "deferred_tasks": {},
                "draining_engines": {},
                "running_tasks": {},
                "engines": engine_payloads,
            }
        ),
        encoding="utf-8",
    )


def _write_explicit_engine_state(path: Path, role: str, engine: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({role: {"engine": engine, "model": "m"}}),
        encoding="utf-8",
    )


@pytest.fixture
def route_queue_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    payload = {
        "repo": "sumipan/nexus",
        "label_namespace": "issuesmith",
        "timezone": "Asia/Tokyo",
        "supported_repos": ["sumipan/nexus"],
        "terminal_labels": ["issuesmith:merge-done"],
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "b1",
                "handler": "brushup",
                "writes_files": False,
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "cp2",
                "handler": "impl",
                "preconditions": ["draft-done"],
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "review",
                "role": "review",
                "entry_step": "r1",
                "handler": "review",
                "preconditions": ["develop-done"],
                "advance_when": ["deps_terminal"],
            },
        ],
        "paths": {
            "queue": "jobs/issuesmith-queue.jsonl",
            "queue_state": "logs/issuesmith-queue-state.json",
            "queue_lock": "logs/issuesmith-queue.lock",
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
                "heavy": [["claude", "claude-sonnet-4-6"]],
                "timeout_sec": 1800,
            },
            "implementation": {
                "heavy": [["claude", "claude-sonnet-4-6"], ["cursor", "auto"]],
                "timeout_sec": 3600,
            },
            "review": {
                "heavy": [["claude", "claude-sonnet-4-6"], ["cursor", "auto"]],
                "timeout_sec": 3600,
            },
        },
        "concurrency": {"default": 1, "per_engine": {"claude": 1, "cursor": 1}},
    }
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield cfg_path
    reset_config_cache()


def _develop_ready_client(issue: int) -> _ProdShapeClient:
    labels = [{"name": "issuesmith:draft-done"}]
    return _ProdShapeClient(
        issues={
            issue: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": list(labels),
            }
        },
        open_issue_rows=[
            {
                "number": issue,
                "title": "t",
                "state": "open",
                "body": _VALID_BODY,
                "labels": list(labels),
            }
        ],
    )


def _review_ready_client(issue: int) -> _ProdShapeClient:
    labels = [{"name": "issuesmith:develop-done"}]
    return _ProdShapeClient(
        issues={
            issue: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": list(labels),
            }
        },
        open_issue_rows=[
            {
                "number": issue,
                "title": "t",
                "state": "open",
                "body": _VALID_BODY,
                "labels": list(labels),
            }
        ],
    )


def _patch_plan_gates(monkeypatch, qmod, quota_path: Path, engine_path: Path) -> None:
    monkeypatch.setattr(qmod, "QUOTA_STATE_PATH", quota_path)
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_path)
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)


def test_missing_state_uses_first_available_route_engine(
    tmp_path, monkeypatch, route_queue_config
):
    """Refs #7837 AC1 — route fallback when claude alone is paused."""
    from issuesmith import queue as qmod

    quota_path = tmp_path / "quota.json"
    engine_path = tmp_path / "missing-engine.yml"
    _write_quota(quota_path, {"claude": "paused", "cursor": "available"})
    _patch_plan_gates(monkeypatch, qmod, quota_path, engine_path)

    store = _store(tmp_path)
    store.enqueue(
        issue=100,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _develop_ready_client(100)
    queue_plan = plan(store, client, now=_FIXED_NOW)
    entry = queue_plan.entries[0]
    assert entry.decision == "dispatch"
    assert entry.engine == "cursor"


def test_explicit_state_engine_remains_pinned_when_paused(
    tmp_path, monkeypatch, route_queue_config
):
    """Refs #7837 AC2 — explicit state engine does not fall back when paused."""
    from issuesmith import queue as qmod

    quota_path = tmp_path / "quota.json"
    engine_path = tmp_path / "engine.yml"
    _write_quota(quota_path, {"claude": "paused", "cursor": "available"})
    _write_explicit_engine_state(engine_path, "implementation", "claude")
    _patch_plan_gates(monkeypatch, qmod, quota_path, engine_path)

    store = _store(tmp_path)
    store.enqueue(
        issue=101,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    queue_plan = plan(store, _develop_ready_client(101), now=_FIXED_NOW)
    entry = queue_plan.entries[0]
    assert entry.decision == "wait"
    assert entry.reason == "engine_paused"
    assert "required engine paused: claude (role=implementation)" in entry.detail
    assert "cursor" not in entry.detail


def test_all_route_engines_paused_lists_every_engine(
    tmp_path, monkeypatch, route_queue_config
):
    """Refs #7837 AC3 — all route candidates paused appear in wait detail."""
    from issuesmith import queue as qmod

    quota_path = tmp_path / "quota.json"
    engine_path = tmp_path / "missing-engine.yml"
    _write_quota(quota_path, {"claude": "paused", "cursor": "paused"})
    _patch_plan_gates(monkeypatch, qmod, quota_path, engine_path)

    store = _store(tmp_path)
    store.enqueue(
        issue=102,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    queue_plan = plan(store, _develop_ready_client(102), now=_FIXED_NOW)
    entry = queue_plan.entries[0]
    assert entry.decision == "wait"
    assert entry.reason == "engine_paused"
    assert "required engine paused: claude, cursor (role=implementation)" in entry.detail


def test_custom_role_dispatch_records_selected_engine(
    tmp_path, monkeypatch, route_queue_config
):
    """Refs #7837 AC4 — custom role uses route fallback for in_flight.engine."""
    from issuesmith import queue as qmod

    quota_path = tmp_path / "quota.json"
    engine_path = tmp_path / "engine.yml"
    _write_quota(quota_path, {"claude": "paused", "cursor": "available"})
    _write_explicit_engine_state(engine_path, "implementation", "cursor")
    _patch_plan_gates(monkeypatch, qmod, quota_path, engine_path)

    store = _store(tmp_path)
    store.enqueue(
        issue=103,
        phase="review",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _review_ready_client(103)
    queue_plan = plan(store, client, now=_FIXED_NOW)
    entry = queue_plan.entries[0]
    assert entry.decision == "dispatch"
    assert entry.engine == "cursor"
    apply(store, client, queue_plan)
    in_flight = store.snapshot().in_flight
    assert len(in_flight) == 1
    assert in_flight[0]["engine"] == "cursor"
    assert in_flight[0]["role"] == "review"
