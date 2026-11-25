"""Regression tests for removing queue enqueue --after (#7963)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import load_config, reset_config_cache
from issuesmith.queue_store import QueueStore

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"
_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")).isoformat()
_JST = ZoneInfo("Asia/Tokyo")

_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)

_AFTER_FEATURE_PATTERNS = (
    re.compile(r"after_deps"),
    re.compile(r'get\("after"\)'),
    re.compile(r'\.get\("after"\)'),
    re.compile(r"add_argument\(\"--after\""),
    re.compile(r"dispatch_blocked"),
    re.compile(r"after:\s*list\[int\]"),
)


@pytest.fixture(autouse=True)
def _clear_config():
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
                "heavy": [["claude", "claude-sonnet-4-6"], ["codex", "gpt-5.6-sol"]],
                "timeout_sec": 1800,
            },
            "implementation": {
                "heavy": [["claude", "claude-sonnet-4-6"], ["cursor", "auto"]],
                "timeout_sec": 3600,
            },
        },
        "concurrency": {"default": 1, "per_engine": {"claude": 2, "cursor": 2, "codex": 2}},
        "phases": [
            {"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"},
            {"name": "sub", "role": "implementation", "entry_step": "sub-ready", "handler": "subissue"},
            {"name": "develop", "role": "implementation", "entry_step": "cp2", "handler": "impl"},
            {"name": "merge", "role": "implementation", "entry_step": "m2", "handler": "merge"},
        ],
        "terminal_labels": ["issuesmith:merge-done", "bump:done"],
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


def _pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH", "")
    return str(SRC_PATH) + (f":{existing}" if existing else "")


def _run_queue(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    run_env = {**os.environ, "PYTHONPATH": _pythonpath()}
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", "queue", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=run_env,
    )


def _patch_dispatch(tmp_path, monkeypatch, issuesmith_config):
    """Set up dispatch/status environment."""
    import issuesmith.config as cfgmod
    import issuesmith.queue as qmod
    import issuesmith.queue_store as qstore

    cfg = load_config(issuesmith_config)
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True, exist_ok=True)
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    exec_path.parent.mkdir(parents=True, exist_ok=True)
    exec_path.write_text("", encoding="utf-8")
    engine_state_path = tmp_path / ".pipeline-state" / "issuesmith-engine.yml"
    engine_state_path.parent.mkdir(parents=True, exist_ok=True)
    engine_state_path.write_text(
        yaml.safe_dump({"design": {"engine": "claude"}, "implementation": {"engine": "cursor"}}),
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
        template_dir=tmp_path / "templates",
        engine_state=engine_state_path,
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
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_state_path)
    monkeypatch.setattr(qstore, "_cfg", patched_cfg)


class _FakeClient:
    def __init__(self, issues: dict[int, dict] | None = None):
        self.issues = issues or {}

    def issue_get(self, number, fields=None):
        data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        data.setdefault("state", "OPEN")
        data.setdefault("title", "t")
        data.setdefault("body", _VALID_BODY)
        data.setdefault("labels", [])
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
        pass

    def issue_comment(self, number, body):
        pass

    def get_issue_comments(self, number):
        return []

    def list_issues(self, label, state="open"):
        return []

    def issue_timeline(self, number):
        return []

    def pr_list(self, *, head=None, state=None, search=None, repo=None, limit=30):
        return []

    def pr_get(self, number, *, repo=None):
        raise RuntimeError(f"PR #{number} not found")

    def api_request(self, path, *, method="GET", fields=None, repo=None, paginate=False):
        if path.startswith("issues"):
            return [
                {
                    "number": n,
                    "title": d.get("title") or "",
                    "state": str(d.get("state", "OPEN")).lower(),
                    "body": d.get("body") or "",
                    "labels": d.get("labels") or [],
                }
                for n, d in self.issues.items()
                if str(d.get("state", "OPEN")).upper() == "OPEN"
            ]
        return []


def test_enqueue_cli_rejects_after_option(issuesmith_config):
    """#7963: queue enqueue does not accept --after."""
    proc = _run_queue(
        "enqueue",
        "--issue",
        "10",
        "--phase",
        "draft",
        "--source",
        "skill",
        "--actor-kind",
        "human",
        "--priority",
        "normal",
        "--requested-by",
        "alice",
        "--after",
        "99",
        env={"ISSUESMITH_CONFIG": str(issuesmith_config)},
    )
    assert proc.returncode != 0
    assert "unrecognized arguments" in proc.stderr or "unrecognized arguments" in proc.stdout


def test_plan_ignores_legacy_request_meta_after(tmp_path, monkeypatch, issuesmith_config):
    """#7963: legacy request_meta.after no longer blocks dispatch planning."""
    _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    result = store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    with store.lock():
        state = store._load_state_unlocked()
        meta = dict(state.get("request_meta") or {})
        meta[result.request_id] = {"after": [99]}
        state["request_meta"] = meta
        store._save_state_unlocked(state)

    client = _FakeClient({50: {"state": "OPEN", "labels": [], "body": _VALID_BODY, "title": "main"}})
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])

    queue_plan = qmod.plan(store, client, now=datetime(2026, 9, 22, 12, 0, tzinfo=_JST))
    by_id = {e.request_id: e for e in queue_plan.entries}
    entry = by_id[result.request_id]
    assert entry.decision == "dispatch"
    assert entry.reason != "after_deps"


def test_status_reads_legacy_after_without_blocked_on(
    tmp_path, monkeypatch, capsys, issuesmith_config
):
    """#7963: status tolerates legacy after meta without surfacing blocked_on."""
    import argparse

    _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    result = store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    with store.lock():
        state = store._load_state_unlocked()
        meta = dict(state.get("request_meta") or {})
        meta[result.request_id] = {"after": [200, 300]}
        state["request_meta"] = meta
        store._save_state_unlocked(state)

    monkeypatch.setattr(qmod, "get_forge", lambda **k: _FakeClient({50: {"body": _VALID_BODY}}))
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    code = qmod._cmd_status(args)
    out = capsys.readouterr().out
    assert code == 0
    assert "issue=#50" in out
    assert "blocked_on" not in out


def test_queue_sources_have_no_after_feature_markers():
    """#7963: queue modules no longer encode enqueue --after / after_deps."""
    hits: list[str] = []
    for rel in ("issuesmith/queue.py", "issuesmith/queue_store.py"):
        text = (SRC_PATH / rel).read_text(encoding="utf-8")
        for pattern in _AFTER_FEATURE_PATTERNS:
            for match in pattern.finditer(text):
                hits.append(f"{rel}:{match.group(0)}")
    assert hits == []
