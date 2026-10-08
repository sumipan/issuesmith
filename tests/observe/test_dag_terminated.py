"""Tests for DagTerminatedEvent detection, evaluate, and execute (AC-1, AC-1b, AC-2, AC-7).

Fix 1 (nexus #4137): DagTerminatedEvent retains in_flight and removes only the -running label.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith.andon import list_open
from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe import (
    _detect_dag_terminated,
    _detect_dag_terminated_local,
    _detect_orphan_exec,
)
from issuesmith.observe.dag_state import DagState, load_dag_states
from issuesmith.observe.events import DagTerminatedEvent, OrphanExecEvent
from issuesmith.observe.policy import (
    ReleaseInFlightAction,
    RemoveRunningLabelAction,
    evaluate,
    execute,
)
from issuesmith.queue_store import QueueStore


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
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


def _make_dirs(tmp_path: Path):
    done_dir = tmp_path / "done"
    done_dir.mkdir(exist_ok=True)
    running_dir = tmp_path / "running"
    running_dir.mkdir(exist_ok=True)
    return done_dir, running_dir


def _dag_failed_states(issue_num: int) -> dict[int, DagState]:
    """Return dag_states dict representing a failed DAG for the given issue."""
    key = f"issuesmith:impl:{issue_num}"
    return {
        issue_num: DagState(
            issue=issue_num,
            key=key,
            status="failed",
            failed_step="p2",
            failed_uuid="p2",
            failed_result_path="/jobs/done/p2",
        )
    }


def _dag_running_states(issue_num: int) -> dict[int, DagState]:
    key = f"issuesmith:impl:{issue_num}"
    return {
        issue_num: DagState(issue=issue_num, key=key, status="running")
    }


def _dag_deferred_states(issue_num: int) -> dict[int, DagState]:
    key = f"issuesmith:impl:{issue_num}"
    return {
        issue_num: DagState(
            issue=issue_num,
            key=key,
            status="deferred",
            deferred_step="p2",
            deferred_uuid="p2",
        )
    }


def test_ac1_dag_terminated_retains_in_flight_and_removes_running_label(env):
    """AC-1: failed DAG -> in_flight retained, running label removed, andon raised.

    Fix 1 (nexus #4137): DagTerminatedEvent must NOT release in_flight.
    Only the -running label is removed so that the allow_paths lock is preserved
    until the DAG recovers (dag recover) or is explicitly reset/abandoned.
    """
    store, client, tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace

    issue_num = client.issue_create("title", "body")
    client.issue_update(issue_num, labels_add=[f"{ns}:draft-done", f"{ns}:develop-running"])
    store.add_in_flight(issue_num, "claude", role="implementation")
    snap = store.snapshot()
    assert any(e.get("issue") == issue_num for e in snap.in_flight)

    dag_states = _dag_failed_states(issue_num)
    events = _detect_dag_terminated(snap, client, cfg, cfg.observe.max_api_calls, dag_states)
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, DagTerminatedEvent)
    assert ev.issue == issue_num
    assert ev.phase == "develop"
    assert ev.failed_step == "p2"

    actions = evaluate([ev], cfg.observe)
    assert not any(isinstance(a, ReleaseInFlightAction) for a in actions), (
        "DagTerminatedEvent must not produce ReleaseInFlightAction"
    )
    assert any(isinstance(a, RemoveRunningLabelAction) for a in actions), (
        "DagTerminatedEvent must produce RemoveRunningLabelAction"
    )
    execute(actions, store, sinks=[], client=client)

    snap2 = store.snapshot()
    assert any(e.get("issue") == issue_num for e in snap2.in_flight), (
        "in_flight must be retained after DagTerminatedEvent"
    )

    labels_after = {
        lbl["name"] if isinstance(lbl, dict) else str(lbl)
        for lbl in client.issue_get(issue_num, fields=["labels"]).get("labels", [])
    }
    assert f"{ns}:develop-running" not in labels_after
    assert f"{ns}:develop-ready" not in labels_after
    assert f"{ns}:draft-done" in labels_after

    open_andons = list_open(client)
    assert len(open_andons) == 1
    key = f"issuesmith:impl:{issue_num}"
    assert f"dag_terminated:{key}" in open_andons[0].id


def test_ac1b_second_observe_does_not_re_raise_andon(env):
    """AC-1b: after release, second tick raises no new andon."""
    store, client, tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace

    issue_num = client.issue_create("title2", "body")
    client.issue_update(issue_num, labels_add=[f"{ns}:draft-done", f"{ns}:develop-running"])
    store.add_in_flight(issue_num, "claude", role="implementation")

    dag_states = _dag_failed_states(issue_num)

    def _run_once():
        s = store.snapshot()
        evts = _detect_dag_terminated(s, client, cfg, cfg.observe.max_api_calls, dag_states)
        actions = evaluate(evts, cfg.observe)
        execute(actions, store, sinks=[], client=client)

    _run_once()
    comments_after_first = client.get_issue_comments(issue_num)
    _run_once()
    comments_after_second = client.get_issue_comments(issue_num)
    assert len(comments_after_second) == len(comments_after_first)


def test_deferred_only_dag_does_not_emit_terminated(env):
    """Deferred-only DAG must not produce DagTerminatedEvent."""
    store, client, _tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace

    issue_num = client.issue_create("deferred only", "body")
    client.issue_update(issue_num, labels_add=[f"{ns}:develop-running"])
    store.add_in_flight(issue_num, "claude", role="implementation")
    snap = store.snapshot()

    dag_states = _dag_deferred_states(issue_num)
    events = _detect_dag_terminated(snap, client, cfg, cfg.observe.max_api_calls, dag_states)
    assert events == []

    local_events = _detect_dag_terminated_local(snap, cfg, dag_states)
    assert local_events == []


def test_failed_and_deferred_dag_still_emits_terminated(env):
    """Failed plus deferred -> DagTerminatedEvent still fires for failed DAG."""
    store, client, _tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace

    issue_num = client.issue_create("failed and deferred", "body")
    client.issue_update(issue_num, labels_add=[f"{ns}:develop-running"])
    store.add_in_flight(issue_num, "claude", role="implementation")
    snap = store.snapshot()

    events = _detect_dag_terminated(
        snap, client, cfg, cfg.observe.max_api_calls, _dag_failed_states(issue_num),
    )
    assert len(events) == 1
    assert isinstance(events[0], DagTerminatedEvent)


def test_ac2_running_dag_not_released(env):
    """AC-2: DAG with p1 running -> no DagTerminatedEvent, in_flight unchanged."""
    store, client, tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace

    issue_num = client.issue_create("title3", "body")
    client.issue_update(issue_num, labels_add=[f"{ns}:develop-running"])
    store.add_in_flight(issue_num, "claude", role="implementation")
    snap = store.snapshot()

    dag_states = _dag_running_states(issue_num)
    events = _detect_dag_terminated(snap, client, cfg, cfg.observe.max_api_calls, dag_states)
    assert events == []

    snap2 = store.snapshot()
    assert any(e.get("issue") == issue_num for e in snap2.in_flight)


def test_ac7_orphan_exec_not_raised_for_running_dag(env):
    """AC-7: _detect_orphan_exec skips exec rows whose issue's DAG is running."""
    store, client, tmp_path = env
    cfg = get_config()

    issue_num = client.issue_create("title4", "body")

    dag_states = _dag_running_states(issue_num)
    snap = store.snapshot()
    orphans = _detect_orphan_exec(snap, cfg, dag_states)
    assert not any(isinstance(e, OrphanExecEvent) and e.issue == issue_num for e in orphans)


def test_ac7_orphan_exec_still_raised_when_no_running_marker(env):
    """AC-7: exec row without running marker IS flagged as orphan."""
    store, client, tmp_path = env
    cfg = get_config()
    _, done_dir, running_dir = (None, *_make_dirs(tmp_path))
    exec_path = tmp_path / "exec.jsonl"

    issue_num = client.issue_create("title5", "body")
    key = f"issuesmith:impl:{issue_num}"
    exec_path.write_text(
        json.dumps({"uuid": "u1", "idempotency_key": key, "depends": []}) + "\n",
        encoding="utf-8",
    )

    dag_states = load_dag_states(exec_path, done_dir, running_dir)

    from dataclasses import replace

    patched_paths = replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir)
    patched_cfg = replace(cfg, paths=patched_paths)

    snap = store.snapshot()
    orphans = _detect_orphan_exec(snap, patched_cfg, dag_states)
    assert any(isinstance(e, OrphanExecEvent) and e.issue == issue_num for e in orphans)


def test_ac1_end_to_end_with_real_done_markers(env):
    """AC-1 via observe(): real exec.jsonl + done markers drive the release."""
    from dataclasses import replace

    from issuesmith.observe import observe

    store, client, tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace
    done_dir, _running_dir = _make_dirs(tmp_path)
    exec_path = tmp_path / "exec.jsonl"

    issue_num = client.issue_create("title6", "body")
    client.issue_update(issue_num, labels_add=[f"{ns}:draft-done", f"{ns}:develop-running"])
    store.add_in_flight(issue_num, "claude", role="implementation")

    key = f"issuesmith:impl:{issue_num}"
    rows = [
        {"uuid": "u-p1", "idempotency_key": key, "depends": [],
         "annotations": {"step_name": "p1"}},
        {"uuid": "u-p2", "idempotency_key": key, "depends": ["u-p1"],
         "annotations": {"step_name": "p2"}},
    ]
    exec_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (done_dir / "u-p1").write_text("0", encoding="utf-8")
    (done_dir / "u-p2").write_text("1", encoding="utf-8")

    patched_cfg = replace(cfg, paths=replace(cfg.paths, exec_jsonl=exec_path, done_dir=done_dir))

    def _tick():
        evts = [
            e for e in observe(store.snapshot(), client, patched_cfg)
            if isinstance(e, DagTerminatedEvent)
        ]
        execute(evaluate(evts, cfg.observe), store, sinks=[], client=client)
        return evts

    evts = _tick()
    assert len(evts) == 1
    assert evts[0].failed_step == "p2"
    assert evts[0].phase == "develop"
    assert any(e.get("issue") == issue_num for e in store.snapshot().in_flight), (
        "in_flight must be retained after DagTerminatedEvent (nexus #4137)"
    )
    labels_after = {
        lbl["name"] if isinstance(lbl, dict) else str(lbl)
        for lbl in client.issue_get(issue_num, fields=["labels"]).get("labels", [])
    }
    assert f"{ns}:develop-running" not in labels_after
    assert f"{ns}:develop-ready" not in labels_after
    assert f"{ns}:draft-done" in labels_after
    assert len(list_open(client)) == 1
    comments_first = client.get_issue_comments(issue_num)

    # Second tick: DAG still failed, issue still in_flight → event fires again but no new comment.
    second_evts = _tick()
    assert len(second_evts) == 1
    assert isinstance(second_evts[0], DagTerminatedEvent)
    assert len(client.get_issue_comments(issue_num)) == len(comments_first)


def test_regression_overlapping_develop_blocked_after_dag_terminated_with_open_pr(env):
    """#3895 AC-3: M1 blocked + DAG terminated + open PR -> overlapping develop blocked."""
    from issuesmith.queue import _allow_paths_conflict

    store, client, _tmp_path = env
    cfg = get_config()
    ns = cfg.label_namespace

    shared_body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tools/asana/tasksmith.py"\n'
        '  - "tests/tools/asana/test_asana_tasksmith.py"\n'
        "```\n"
    )
    blocked_issue = client.issue_create("blocked sibling", shared_body)
    next_issue = client.issue_create("next sibling", shared_body)

    client.issue_update(
        blocked_issue,
        labels_add=[f"{ns}:draft-done", f"{ns}:develop-running"],
    )
    store.add_in_flight(
        blocked_issue,
        "claude",
        role="implementation",
        phase="develop",
        allow_paths=(
            "tools/asana/tasksmith.py",
            "tests/tools/asana/test_asana_tasksmith.py",
        ),
        target_repo="sumipan/issuesmith",
    )

    dag_states = _dag_failed_states(blocked_issue)
    snap = store.snapshot()
    events = _detect_dag_terminated(
        snap, client, cfg, cfg.observe.max_api_calls, dag_states
    )
    assert len(events) == 1
    execute(evaluate(events, cfg.observe), store, sinks=[], client=client)

    assert any(e.get("issue") == blocked_issue for e in store.snapshot().in_flight), (
        "in_flight must stay locked after DagTerminatedEvent while PR is still open"
    )

    conflict = _allow_paths_conflict(
        "sumipan/issuesmith",
        (
            "tools/asana/tasksmith.py",
            "tests/tools/asana/test_asana_tasksmith.py",
        ),
        store.snapshot().in_flight,
        candidate_phase="develop",
    )
    assert conflict == blocked_issue

    # Simulate an open PR still linked to the blocked issue (#3781 / PR #3892 scenario).
    client.pr_create(
        title=f"tasksmith Refs #{blocked_issue}",
        body=f"Refs #{blocked_issue}",
        head=f"issue-{blocked_issue}-1364c267",
        base="main",
    )

    assert _allow_paths_conflict(
        "sumipan/issuesmith",
        ("tools/asana/tasksmith.py",),
        store.snapshot().in_flight,
        candidate_phase="develop",
    ) == blocked_issue
    assert next_issue != blocked_issue
