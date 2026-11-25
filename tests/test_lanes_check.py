"""Lanes check API and CLI (#8042)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import UserDict
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import yaml

import tests.conftest as conftest_mod
from issuesmith.config import IssuesmithConfig, load_config, reset_config_cache
from issuesmith.lanes import (
    IssueView,
    Ledger,
    check,
    check_to_dict,
    fetch_issue_views,
    load_ledger,
    plan,
    plan_to_dict,
)
from issuesmith.queue import PlanEntry, QueuePlan
from issuesmith.queue_store import QueueRequest, QueueSnapshot

_T0 = datetime(2026, 6, 15, 10, 30, tzinfo=timezone.utc)


def _cfg(tmp_path: Path, *, timezone_name: str = "UTC") -> IssuesmithConfig:
    path = tmp_path / "issuesmith.yaml"
    path.write_text(
        yaml.safe_dump({"repo": "org/repo", "timezone": timezone_name}),
        encoding="utf-8",
    )
    reset_config_cache()
    return load_config(path)


def _snapshot(
    *,
    in_flight: list[dict] | None = None,
    active: list[tuple[int, str, str]] | None = None,
) -> QueueSnapshot:
    requests: dict[str, QueueRequest] = {}
    order: list[str] = []
    for idx, (issue, phase, requested_at) in enumerate(active or []):
        rid = f"r{idx}"
        requests[rid] = QueueRequest(
            request_id=rid,
            issue=issue,
            phase=phase,
            source="test",
            actor_kind="automation",
            priority="normal",
            requested_at=requested_at,
            requested_by=("test",),
        )
        order.append(rid)
    return QueueSnapshot(
        schema_version=1,
        revision=0,
        active_order=order,
        completed_request_ids=[],
        last_triaged_revision=0,
        halt=False,
        halt_reason=None,
        requests=requests,
        in_flight=list(in_flight or []),
    )


def test_check_lane_status_heads(tmp_path: Path):
    """Refs #8042 AC1."""
    cfg = _cfg(tmp_path)
    ledger = Ledger(
        lanes={"a": [1, 2], "b": [3], "c": [4], "d": [5], "e": [6], "f": []},
        hold={4},
    )
    issues = {
        1: IssueView(1, (), "closed"),
        2: IssueView(2, ()),
        3: IssueView(3, ()),
        5: IssueView(5, ("issuesmith:andon-decision",)),
    }
    snap = _snapshot(in_flight=[{"issue": 3, "engine": "claude"}])
    role_map = {"design": "claude", "implementation": "cursor"}
    result = check(
        ledger,
        snap,
        issues,
        cfg,
        _T0,
        role_engine_map=role_map,
    )
    first_phase = cfg.phases[0].name
    assert result.lane_status["a"] == f"ready #2 ({first_phase})"
    assert result.lane_status["b"] == "busy #3"
    assert result.lane_status["c"] == "hold #4"
    assert result.lane_status["d"] == "blocked #5"
    assert result.lane_status["e"] == "unknown #6"
    assert result.lane_status["f"] == "empty"


def test_check_snapshot_views_and_queued_reasons(tmp_path: Path):
    """Refs #8042 AC2."""
    cfg = _cfg(tmp_path)
    ledger = Ledger(lanes={})
    snap = _snapshot(
        in_flight=[
            {"issue": 7, "engine": "claude"},
            {"issue": 8, "engine": "claude", "held": {"by": "x", "at": "t"}},
        ],
        active=[(9, "develop", _T0.isoformat())],
    )
    entries = (
        PlanEntry("r1", 9, "develop", "cursor", "wait", "slot_full", "slot_full: x"),
        PlanEntry("h", 0, "", None, "wait", "halt", "halted for maintenance"),
    )
    queue_plan = QueuePlan(revision=1, entries=entries, halt=True)
    result = check(
        ledger,
        snap,
        {},
        cfg,
        _T0,
        role_engine_map={"design": "claude", "implementation": "cursor"},
        queue_plan=queue_plan,
    )
    assert result.in_flight == [7]
    assert result.held == [8]
    assert result.queued == [9]
    assert result.queued_reasons == {9: "slot_full: x"}


def test_fetch_issue_views_prefers_cache_and_skips_failures():
    """Refs #8042 AC3."""
    forge_calls: list[int] = []

    def _issue_get(number, fields=None):
        forge_calls.append(number)
        if number == 3:
            raise RuntimeError("missing")
        return UserDict(
            {
                "number": 2,
                "state": "open",
                "labels": [{"name": "from-forge"}],
            }
        )

    client = MagicMock()
    client.issue_get.side_effect = _issue_get

    cache = {1: {"labels": [{"name": "x"}], "state": "open"}}
    views = fetch_issue_views(client, [2, 1, 2, 3], cache=cache)
    assert set(views) == {1, 2}
    assert views[1].labels == ("x",)
    assert views[2].labels == ("from-forge",)
    assert forge_calls == [2, 3]


def test_check_to_dict_preserves_plan_and_adds_metadata(tmp_path: Path):
    """Refs #8042 AC4."""
    cfg = _cfg(tmp_path, timezone_name="Asia/Tokyo")
    ledger = Ledger(lanes={"a": [1]})
    issues = {1: IssueView(1, ())}
    snap = _snapshot()
    result = check(
        ledger,
        snap,
        issues,
        cfg,
        _T0,
        role_engine_map={"design": "claude", "implementation": "cursor"},
        api_remaining=799,
        api_low=True,
    )
    payload = check_to_dict(result)
    assert {k: payload[k] for k in plan_to_dict(result.plan)} == plan_to_dict(result.plan)
    assert payload["api_remaining"] == 799
    assert payload["api_low"] is True
    assert payload["has_work"] is True
    assert payload["generated_at"].endswith("+09:00")
    assert all(isinstance(k, str) for k in payload["queued_reasons"])


def _lanes_cli_env(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    forge_root = tmp_path / "forge"
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    lanes_path = tmp_path / "lanes.yaml"
    lanes_path.write_text("lanes:\n  a: [1]\n", encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    payload: dict = {
        "repo": "org/repo",
        "timezone": "Asia/Tokyo",
        "paths": {
            "lanes": str(lanes_path),
            "queue_state": str(queue_dir / "state.json"),
            "queue_lock": str(queue_dir / "lock"),
            "exec_jsonl": str(tmp_path / "jobs" / "exec.jsonl"),
            "done_dir": str(tmp_path / "jobs" / "done"),
        },
    }
    for key, value in conftest_mod._TEST_DECLARATION.items():
        payload.setdefault(key, value)
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    (tmp_path / "jobs").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(queue_dir))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(forge_root))
    reset_config_cache()
    return cfg_path, forge_root


def test_cli_check_out_matches_json_stdout(tmp_path: Path, monkeypatch):
    """Refs #8042 AC5."""
    _lanes_cli_env(tmp_path, monkeypatch)
    from ghdag.forge import get_forge

    client = get_forge()
    client.issue_create("candidate", "body")

    out_path = tmp_path / "out.json"
    worktree_src = Path(__file__).resolve().parents[1] / "src"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "issuesmith",
            "lanes",
            "check",
            "--json",
            "--out",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(worktree_src),
            "ISSUESMITH_CONFIG": str(tmp_path / "issuesmith.yaml"),
            "ISSUESMITH_QUEUE_DIR": str(tmp_path / "queue"),
            "GHDAG_FORGE": "local",
            "GHDAG_FORGE_ROOT": str(tmp_path / "forge"),
        },
    )
    assert proc.returncode == 0, proc.stderr
    stdout_doc = json.loads(proc.stdout)
    file_doc = json.loads(out_path.read_text(encoding="utf-8"))
    assert stdout_doc == file_doc
    assert file_doc["enqueue"][0]["issue"] == 1


def test_cli_check_apply_skips_when_halted(tmp_path: Path, monkeypatch):
    """Refs #8042 AC6."""
    _lanes_cli_env(tmp_path, monkeypatch)
    from ghdag.forge import get_forge

    from issuesmith.queue import QueuePlan
    from issuesmith.queue_store import QueueStore

    get_forge().issue_create("candidate", "body")
    store = QueueStore()
    before = len(store.snapshot().active_requests)

    halted = QueuePlan(
        revision=0,
        entries=(
            PlanEntry("h", 0, "", None, "wait", "halt", "stopped"),
        ),
        halt=True,
    )

    monkeypatch.setattr("issuesmith.queue.plan", lambda *_a, **_k: halted)
    from issuesmith.cli import _cmd_lanes

    assert _cmd_lanes(["check", "--apply", "--json"]) == 0
    after = len(store.snapshot().active_requests)
    assert before == after == 0


def test_load_ledger_ignores_auto_lanes_and_limits_plan_to_registered_issues(
    tmp_path: Path,
):
    """Refs #8042 AC7."""
    path = tmp_path / "lanes.yaml"
    path.write_text(
        "auto_lanes:\n  enabled: true\nlanes:\n  main: [100]\n",
        encoding="utf-8",
    )
    ledger = load_ledger(path)
    assert "auto_lanes_enabled" not in {f.name for f in fields(ledger)}
    assert {f.name for f in fields(IssueView)} == {"number", "labels", "state"}

    cfg = _cfg(tmp_path)
    registered = {100: IssueView(100, ())}
    unregistered = {200: IssueView(200, ())}
    lane_plan = plan(
        ledger,
        _snapshot(),
        {**registered, **unregistered},
        cfg,
        _T0,
        False,
        role_engine_map={"design": "claude", "implementation": "cursor"},
    )
    candidates = {row["issue"] for row in lane_plan.candidates}
    assert 100 in candidates or any(e["issue"] == 100 for e in lane_plan.enqueue)
    assert 200 not in candidates
    assert not any(e["issue"] == 200 for e in lane_plan.enqueue)
