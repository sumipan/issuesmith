"""Failure-mark driven dag_terminated detection (refs #8073)."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import Andon, list_open, to_comment
from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe import observe
from issuesmith.observe.events import DagTerminatedEvent, ObserveFailureWatchSeedEvent
from issuesmith.observe.policy import evaluate, execute
from issuesmith.queue_store import QueueStore

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/repo",
                "observe": {
                    "max_api_calls": 20,
                    "task_timeout_minutes": 1,
                    "workflows": [{"name": "bump"}],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "q.lock",
    )
    client = get_forge()
    yield store, client, tmp_path
    reset_config_cache()


def _dirs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    done_dir = tmp_path / "done"
    done_dir.mkdir(exist_ok=True)
    running_dir = tmp_path / "running"
    running_dir.mkdir(exist_ok=True)
    quota_dir = tmp_path / "quota-waiting"
    quota_dir.mkdir(exist_ok=True)
    exec_path = tmp_path / "exec.jsonl"
    return exec_path, done_dir, running_dir, quota_dir


def _patch_cfg(tmp_path: Path, exec_path: Path, done_dir: Path):
    cfg = get_config()
    return replace(cfg, paths=replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir))


def _now() -> datetime:
    return _NOW


def _write_failed_main(
    exec_path: Path,
    done_dir: Path,
    issue: int,
    *,
    uuids: tuple[str, str] = ("u1", "u2"),
    when: datetime | None = None,
) -> str:
    key = f"issuesmith:impl:{issue}"
    rows = [
        {"uuid": uuids[0], "idempotency_key": key, "depends": [], "annotations": {"step_name": "p1"}},
        {"uuid": uuids[1], "idempotency_key": key, "depends": [uuids[0]], "annotations": {"step_name": "p2"}},
    ]
    exec_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (done_dir / uuids[0]).write_text("0", encoding="utf-8")
    (done_dir / uuids[1]).write_text("1", encoding="utf-8")
    failed = uuids[1]
    ts = (when or _now()).timestamp()
    os.utime(done_dir / failed, (ts, ts))
    return failed


def _write_failed_bump(exec_path: Path, done_dir: Path, issue: int, *, when: datetime | None = None) -> str:
    key = f"bump:bump:{issue}:0"
    rows = [
        {"uuid": "b1", "idempotency_key": key, "depends": [], "annotations": {"step_name": "bump"}},
        {"uuid": "b2", "idempotency_key": key, "depends": ["b1"], "annotations": {"step_name": "post"}},
    ]
    exec_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (done_dir / "b1").write_text("0", encoding="utf-8")
    (done_dir / "b2").write_text("1", encoding="utf-8")
    ts = (when or _now()).timestamp()
    os.utime(done_dir / "b2", (ts, ts))
    return "b2"


def _seed(store, client, cfg, when: datetime | None = None):
    tick = when or _now()
    evts = observe(store.snapshot(), client, cfg, now=tick)
    assert any(isinstance(e, ObserveFailureWatchSeedEvent) for e in evts)
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    return tick


def test_first_tick_seeds_failure_ledger_without_actions(env):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("seed", "body")
    seed_time = _now()
    _write_failed_main(exec_path, done_dir, issue, when=seed_time)
    evts = observe(store.snapshot(), client, cfg, now=seed_time)
    assert len(evts) == 1
    assert isinstance(evts[0], ObserveFailureWatchSeedEvent)
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    assert not list_open(client)
    snap = store.snapshot()
    assert snap.observe_failure_watch_since
    assert snap.observe_failure_seen


@pytest.mark.parametrize(
    "writer",
    [
        lambda p, d, i, w: _write_failed_main(p, d, i, when=w),
        lambda p, d, i, w: _write_failed_bump(p, d, i, when=w),
    ],
)
def test_new_failure_raises_once_without_running_label(env, writer):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("open fail", "body")
    seed_time = _seed(store, client, cfg)
    fail_time = seed_time + timedelta(seconds=5)
    writer(exec_path, done_dir, issue, fail_time)
    later = fail_time + timedelta(seconds=5)
    evts = [
        e
        for e in observe(store.snapshot(), client, cfg, now=later)
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    assert len(evts) == 1
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    ev = evts[0]
    if ev.namespace:
        labels = client.issue_get(issue, fields=["labels"]).get("labels", [])
        names = {lbl["name"] if isinstance(lbl, dict) else str(lbl) for lbl in labels}
        assert f"{ev.namespace}:andon-blocked" in names
    else:
        assert len(list_open(client)) == 1
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    if not ev.namespace:
        assert len(list_open(client)) == 1


def test_completed_closed_issue_is_reopened_before_andon(env):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("closed", "body")
    client.issue_close(issue, reason="completed")
    seed_time = _seed(store, client, cfg)
    fail_time = seed_time + timedelta(seconds=5)
    _write_failed_main(exec_path, done_dir, issue, when=fail_time)
    later = fail_time + timedelta(seconds=5)
    evts = [
        e
        for e in observe(store.snapshot(), client, cfg, now=later)
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    assert client.issue_get(issue, fields=["state"])["state"] == "OPEN"
    assert len(list_open(client)) == 1


def test_reopen_guard_and_not_planned_branch(env):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue_guard = client.issue_create("guard", "body")
    client.issue_close(issue_guard, reason="completed")
    seed_time = _seed(store, client, cfg)
    fail_time = seed_time + timedelta(seconds=5)
    _write_failed_main(exec_path, done_dir, issue_guard, when=fail_time)
    t1 = fail_time + timedelta(seconds=5)
    evts = [
        e
        for e in observe(store.snapshot(), client, cfg, now=t1)
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    client.issue_close(issue_guard, reason="completed")
    fail2_time = t1 + timedelta(seconds=5)
    _write_failed_main(exec_path, done_dir, issue_guard, uuids=("g1", "g2"), when=fail2_time)
    t2 = fail2_time + timedelta(seconds=5)
    evts2 = [
        e
        for e in observe(store.snapshot(), client, cfg, now=t2)
        if isinstance(e, DagTerminatedEvent) and e.reopen_guard
    ]
    assert len(evts2) == 1
    before_comments = len(client.get_issue_comments(issue_guard))
    execute(evaluate(evts2, cfg.observe), store, sinks=[], client=client)
    assert client.issue_get(issue_guard, fields=["state"])["state"] == "CLOSED"
    assert len(client.get_issue_comments(issue_guard)) > before_comments
    issue_np = client.issue_create("not planned", "body")
    client.issue_close(issue_np, reason="not_planned")
    fail3_time = t2 + timedelta(seconds=5)
    _write_failed_main(exec_path, done_dir, issue_np, uuids=("n1", "n2"), when=fail3_time)
    t3 = fail3_time + timedelta(seconds=5)
    evts3 = [
        e
        for e in observe(store.snapshot(), client, cfg, now=t3)
        if isinstance(e, DagTerminatedEvent) and e.issue == issue_np and not e.sync_only
    ]
    before = len(client.get_issue_comments(issue_np))
    execute(evaluate(evts3, cfg.observe), store, sinks=[], client=client)
    assert len(client.get_issue_comments(issue_np)) == before
    assert not any(a.issue == issue_np for a in list_open(client))


def test_nearby_pipeline_andon_suppresses_observe_andon(env, monkeypatch):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("pipeline", "body")
    seed_time = _seed(store, client, cfg)
    fail_time = seed_time + timedelta(seconds=5)
    failed = _write_failed_main(exec_path, done_dir, issue, when=fail_time)
    pipeline = Andon(
        id=f"issuesmith:{issue}:cp1:0",
        kind="blocked",
        issue=issue,
        step="cp1",
        summary="cp1 failed",
    )
    client.issue_comment(issue, to_comment(pipeline))
    posted_at = fail_time.isoformat().replace("+00:00", "Z")
    original_comments = client.get_issue_comments

    def _comments_at_tick(issue_num: int) -> list[dict]:
        return [
            {**row, "created_at": posted_at}
            for row in original_comments(issue_num)
        ]

    monkeypatch.setattr(client, "get_issue_comments", _comments_at_tick)
    later = fail_time + timedelta(seconds=5)
    evts = [
        e
        for e in observe(store.snapshot(), client, cfg, now=later)
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    assert not any(a.issue == issue for a in list_open(client))
    assert failed in store.snapshot().observe_failure_seen


def test_api_budget_defers_failure_without_marking_seen(env):
    store, client, tmp_path = env
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "observe": {"max_api_calls": 0}}),
        encoding="utf-8",
    )
    reset_config_cache()
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("budget", "body")
    seed_time = _seed(store, client, cfg)
    fail_time = seed_time + timedelta(seconds=5)
    failed = _write_failed_main(exec_path, done_dir, issue, when=fail_time)
    later = fail_time + timedelta(seconds=5)
    evts = [
        e
        for e in observe(store.snapshot(), client, cfg, now=later)
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    assert not evts
    assert failed not in store.snapshot().observe_failure_seen


def test_comment_read_failure_defers_failure_without_marking_seen(env, monkeypatch):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("read failure", "body")
    seed_time = _seed(store, client, cfg)
    fail_time = seed_time + timedelta(seconds=5)
    failed = _write_failed_main(exec_path, done_dir, issue, when=fail_time)
    original = client.get_issue_comments

    def _fail(_issue: int) -> list[dict]:
        raise RuntimeError("temporary forge failure")

    monkeypatch.setattr(client, "get_issue_comments", _fail)
    later = fail_time + timedelta(seconds=5)
    assert not [
        e
        for e in observe(store.snapshot(), client, cfg, now=later)
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    assert failed not in store.snapshot().observe_failure_seen

    monkeypatch.setattr(client, "get_issue_comments", original)
    retried = [
        e
        for e in observe(store.snapshot(), client, cfg, now=later + timedelta(seconds=5))
        if isinstance(e, DagTerminatedEvent) and not e.sync_only
    ]
    assert len(retried) == 1


def test_unstarted_timeout_respects_markers_and_dependencies(env):
    store, client, tmp_path = env
    exec_path, done_dir, running_dir, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("unstarted", "body")
    key = f"issuesmith:impl:{issue}"
    rows = [
        {"uuid": "d1", "idempotency_key": key, "depends": [], "annotations": {"step_name": "p1"}},
        {"uuid": "d2", "idempotency_key": key, "depends": ["d1"], "annotations": {"step_name": "p2"}},
    ]
    exec_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (done_dir / "d1").write_text("0", encoding="utf-8")
    seed_time = _seed(store, client, cfg)
    t_short = seed_time + timedelta(seconds=30)
    short_evts = observe(store.snapshot(), client, cfg, now=t_short)
    execute(evaluate(short_evts, cfg.observe), store, sinks=[], client=client)
    assert not [e for e in short_evts if e.kind == "unstarted_task"]
    t_long = seed_time + timedelta(minutes=2)
    evts = [e for e in observe(store.snapshot(), client, cfg, now=t_long) if e.kind == "unstarted_task"]
    assert len(evts) == 1
    execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
    assert any(a.id.endswith(":unstarted:d2:0") for a in list_open(client))
    (running_dir / "d2.json").write_text("{}", encoding="utf-8")
    t_clear = t_long + timedelta(seconds=30)
    clear_evts = observe(store.snapshot(), client, cfg, now=t_clear)
    execute(evaluate(clear_evts, cfg.observe), store, sinks=[], client=client)
    assert "d2" not in dict(store.snapshot().observe_unstarted_first_seen)


def test_unstarted_ledger_drops_uuid_removed_from_latest_generation(env):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("generation", "body")
    old_key = f"issuesmith:impl:{issue}:0"
    exec_path.write_text(
        json.dumps({"uuid": "old", "idempotency_key": old_key, "depends": []}) + "\n",
        encoding="utf-8",
    )
    seed_time = _seed(store, client, cfg)
    first_seen_events = observe(
        store.snapshot(), client, cfg, now=seed_time + timedelta(seconds=5),
    )
    execute(evaluate(first_seen_events, cfg.observe), store, sinks=[], client=client)
    assert "old" in dict(store.snapshot().observe_unstarted_first_seen)

    new_key = f"issuesmith:impl:{issue}:1"
    with exec_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"uuid": "new", "idempotency_key": new_key, "depends": []}) + "\n")
    next_events = observe(
        store.snapshot(), client, cfg, now=seed_time + timedelta(seconds=10),
    )
    execute(evaluate(next_events, cfg.observe), store, sinks=[], client=client)
    ledger = dict(store.snapshot().observe_unstarted_first_seen)
    assert "old" not in ledger
    assert "new" in ledger


def test_unstarted_tracks_latest_generation_per_workflow(env):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("parallel workflows", "body")
    rows = [
        {"uuid": "main", "idempotency_key": f"issuesmith:impl:{issue}:0", "depends": []},
        {"uuid": "bump", "idempotency_key": f"bump:bump:{issue}:0", "depends": []},
    ]
    exec_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )
    seed_time = _seed(store, client, cfg)
    first_seen_events = observe(
        store.snapshot(), client, cfg, now=seed_time + timedelta(seconds=5),
    )
    execute(evaluate(first_seen_events, cfg.observe), store, sinks=[], client=client)
    assert set(dict(store.snapshot().observe_unstarted_first_seen)) == {"main", "bump"}

    timeout_events = observe(
        store.snapshot(), client, cfg, now=seed_time + timedelta(minutes=2),
    )
    unstarted = [event for event in timeout_events if event.kind == "unstarted_task"]
    assert {event.uuid for event in unstarted} == {"main", "bump"}
    assert {event.namespace for event in unstarted} == {"", "bump"}


def test_closed_unstarted_task_comment_uses_timeout_time_and_no_marker(env):
    store, client, tmp_path = env
    exec_path, done_dir, _running, _quota = _dirs(tmp_path)
    cfg = _patch_cfg(tmp_path, exec_path, done_dir)
    issue = client.issue_create("closed unstarted", "body")
    client.issue_close(issue, reason="completed")
    key = f"issuesmith:impl:{issue}"
    exec_path.write_text(
        json.dumps({"uuid": "late", "idempotency_key": key, "depends": []}) + "\n",
        encoding="utf-8",
    )
    seed_time = _seed(store, client, cfg)
    first_seen = seed_time + timedelta(seconds=5)
    initial_events = observe(store.snapshot(), client, cfg, now=first_seen)
    execute(evaluate(initial_events, cfg.observe), store, sinks=[], client=client)

    timeout_at = first_seen + timedelta(minutes=2)
    timeout_events = observe(store.snapshot(), client, cfg, now=timeout_at)
    execute(evaluate(timeout_events, cfg.observe), store, sinks=[], client=client)

    assert client.issue_get(issue, fields=["state"])["state"] == "OPEN"
    comment = client.get_issue_comments(issue)[-2]["body"]
    assert "result_line=no completion marker" in comment
    assert f"failed_at={timeout_at.isoformat()}" in comment
