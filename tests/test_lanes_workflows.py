"""Workflow-scoped lane ledger selection (#8043)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import tests.conftest as conftest_mod
from issuesmith.config import ConfigError, reset_config_cache
from issuesmith.lanes import Ledger, load_ledger, select_workflow


def _workflow_fixture() -> Ledger:
    return Ledger(
        lanes={"a": [1, 2], "b": [2, 3]},
        hold={1, 2, 3},
        backlog=[3, 1, 2],
        high={1, 2},
        workflows={"factory": [2], "docs": [3]},
        default_workflow="issuesmith",
    )


def test_select_default_keeps_unlisted():
    """Refs #8043 AC1."""
    ledger = _workflow_fixture()
    lanes_before = {name: list(nums) for name, nums in ledger.lanes.items()}
    hold_before = set(ledger.hold)
    backlog_before = list(ledger.backlog)
    high_before = set(ledger.high)

    selected = select_workflow(ledger, "issuesmith")

    assert selected.lanes == {"a": [1], "b": []}
    assert selected.hold == {1}
    assert selected.backlog == [1]
    assert selected.high == {1}
    assert ledger.lanes == lanes_before
    assert ledger.hold == hold_before
    assert ledger.backlog == backlog_before
    assert ledger.high == high_before


def test_select_named_keeps_listed():
    """Refs #8043 AC2."""
    selected = select_workflow(_workflow_fixture(), "factory")
    assert selected.lanes == {"a": [2], "b": [2]}
    assert selected.hold == {2}
    assert selected.backlog == [2]
    assert selected.high == {2}


def test_select_unknown_namespace_is_empty():
    """Refs #8043 AC3."""
    selected = select_workflow(_workflow_fixture(), "unknown")
    assert selected.lanes == {"a": [], "b": []}
    assert selected.hold == set()
    assert selected.backlog == []
    assert selected.high == set()


def test_select_without_workflows_is_identity():
    """Refs #8043 AC4."""
    ledger = Ledger(lanes={"a": [1]})
    assert select_workflow(ledger, "anything") is ledger


def test_load_ledger_normalizes_workflows(tmp_path: Path):
    """Refs #8043 AC5."""
    path = tmp_path / "lanes.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "default_workflow": "factory",
                "workflows": {
                    "docs": [2, "3", 3, 2.5, 0, -1, True, "bad"],
                    "ignored": 4,
                },
                "lanes": {},
            }
        ),
        encoding="utf-8",
    )
    ledger = load_ledger(path)
    assert ledger.default_workflow == "factory"
    assert ledger.workflows == {"docs": [2, 3]}


@pytest.mark.parametrize(
    "body,needles",
    [
        (
            {"default_workflow": "issuesmith", "workflows": {"issuesmith": [1]}, "lanes": {}},
            ("issuesmith", "#1"),
        ),
        (
            {"workflows": {"alpha": [1], "beta": [1]}, "lanes": {}},
            ("alpha", "beta", "#1"),
        ),
    ],
)
def test_load_ledger_rejects_ambiguous_workflow_ownership(
    tmp_path: Path, body: dict, needles: tuple[str, ...]
):
    """Refs #8043 AC6."""
    path = tmp_path / "lanes.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        load_ledger(path)
    for needle in needles:
        assert needle in str(exc.value)


def _factory_lanes_cli_env(tmp_path: Path, monkeypatch) -> Path:
    forge_root = tmp_path / "forge"
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    lanes_path = tmp_path / "lanes.yaml"
    lanes_path.write_text(
        yaml.safe_dump(
            {
                "lanes": {"a": [1, 2]},
                "workflows": {"factory": [2]},
            }
        ),
        encoding="utf-8",
    )
    cfg_path = tmp_path / "issuesmith.yaml"
    payload: dict = {
        "repo": "org/repo",
        "timezone": "Asia/Tokyo",
        "label_namespace": "factory",
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
    return cfg_path


def _run_lanes_cli(tmp_path: Path, cfg_path: Path, subcmd: str) -> dict:
    worktree_src = Path(__file__).resolve().parents[1] / "src"
    forge_root = tmp_path / "forge"
    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith", "lanes", subcmd, "--json"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(worktree_src),
            "ISSUESMITH_CONFIG": str(cfg_path),
            "ISSUESMITH_QUEUE_DIR": str(tmp_path / "queue"),
            "GHDAG_FORGE": "local",
            "GHDAG_FORGE_ROOT": str(forge_root),
        },
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_cli_check_uses_label_namespace(tmp_path: Path, monkeypatch):
    """Refs #8043 AC7."""
    cfg_path = _factory_lanes_cli_env(tmp_path, monkeypatch)
    from ghdag.forge import get_forge

    get_forge().issue_create("one", "body")
    get_forge().issue_create("two", "body")
    payload = _run_lanes_cli(tmp_path, cfg_path, "check")
    enqueue_issues = [row["issue"] for row in payload["enqueue"]]
    assert enqueue_issues == [2]


def test_cli_report_uses_label_namespace(tmp_path: Path, monkeypatch):
    """Refs #8043 AC8."""
    cfg_path = _factory_lanes_cli_env(tmp_path, monkeypatch)
    from ghdag.forge import get_forge

    get_forge().issue_create("one", "body")
    get_forge().issue_create("two", "body")
    payload = _run_lanes_cli(tmp_path, cfg_path, "report")
    enqueue_issues = [row["issue"] for row in payload["enqueue"]]
    assert enqueue_issues == [2]
    for key in ("candidates", "enqueue", "waits"):
        for row in payload.get(key) or []:
            assert row.get("issue") != 1
