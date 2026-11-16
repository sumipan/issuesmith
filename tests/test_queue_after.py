"""test_queue_after.py - AC-1 to AC-5: --after CLI option and blocked_on visibility."""

from __future__ import annotations

import argparse
import os
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


def _run_queue(*args: str, tmp_path: Path, env: dict | None = None) -> subprocess.CompletedProcess[str]:
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


class TestEnqueueAfterStore:
    """AC-1: --after saves issue numbers in request_meta."""


    def test_after_merge_deduplicates(self, tmp_path):
        store = _store(tmp_path)
        r1 = store.enqueue(
            issue=100,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
            after=[200, 300],
        )
        store.enqueue(
            issue=100,
            phase="draft",
            source="skill2",
            actor_kind="human",
            priority="normal",
            requested_by=["bob"],
            requested_at=_NOW,
            after=[300, 400],
        )
        snap = store.snapshot()
        meta = snap.request_meta.get(r1.request_id) or {}
        assert meta.get("after") == [200, 300, 400]


class _FakeClient:
    """Minimal fake client for dispatch tests."""

    def __init__(self, issues: dict[int, dict]):
        self.issues = issues
        self.updates: list[tuple] = []
        self.comments: list[tuple] = []

    def issue_get(self, number, fields=None):
        data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        data.setdefault("state", "OPEN")
        data.setdefault("title", "t")
        data.setdefault("body", _VALID_BODY)
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


def _patch_dispatch(tmp_path, monkeypatch, issuesmith_config):
    """Set up dispatch_one environment."""
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


class TestDispatchAfterBlocking:
    """AC-2: after deps block dispatch when unresolved."""


    def test_after_closed_merge_done_dep_allows_dispatch(self, tmp_path, monkeypatch, issuesmith_config):
        _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
            after=[99],
        )
        store.mark_triaged(store.snapshot().revision)
        client = _FakeClient(
            {
                50: {"state": "OPEN", "labels": [], "body": _VALID_BODY, "title": "main issue"},
                99: {
                    "state": "CLOSED",
                    "labels": [{"name": "issuesmith:merge-done"}],
                    "body": "",
                    "title": "dep issue",
                },
            }
        )
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
        now = datetime(2026, 9, 22, 12, 0, tzinfo=_JST)
        result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
        assert result.dispatched is True
        assert result.issue == 50


class TestStatusAfterDisplay:
    """AC-1 (display) and AC-5: status shows blocked_on for after deps."""

    def test_status_shows_after_list(self, tmp_path, monkeypatch, capsys, issuesmith_config):
        # AC-1: after=[M, K] visible in queue status
        _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
            after=[200, 300],
        )

        monkeypatch.setattr(qmod, "get_forge", lambda **k: None)
        args = argparse.Namespace(
            queue_path=str(store.queue_path),
            state_path=str(store.state_path),
            lock_path=str(store.lock_path),
        )
        qmod._cmd_status(args)
        out = capsys.readouterr().out
        assert "200" in out and "300" in out
        assert "blocked_on" in out or "after" in out

    def test_status_shows_blocked_on_for_open_dep(self, tmp_path, monkeypatch, capsys, issuesmith_config):
        # AC-5: blocked_on shown when after dep is OPEN
        _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
            after=[99],
        )

        fake_client = _FakeClient(
            {
                50: {"state": "OPEN", "labels": [], "body": _VALID_BODY},
                99: {"state": "OPEN", "labels": [], "body": "", "title": "dep"},
            }
        )
        monkeypatch.setattr(qmod, "get_forge", lambda **k: fake_client)
        args = argparse.Namespace(
            queue_path=str(store.queue_path),
            state_path=str(store.state_path),
            lock_path=str(store.lock_path),
        )
        qmod._cmd_status(args)
        out = capsys.readouterr().out
        assert "blocked_on" in out
        assert "#99" in out


class TestDoctorDispatchBlocked:
    """AC-5: doctor shows dispatch_blocked count."""

    def test_doctor_shows_dispatch_blocked_count(self, tmp_path, monkeypatch, capsys, issuesmith_config):
        _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
            after=[99],
        )

        fake_client = _FakeClient(
            {
                50: {"state": "OPEN", "labels": [], "body": _VALID_BODY, "title": "main"},
                99: {"state": "OPEN", "labels": [], "body": "", "title": "dep"},
            }
        )
        monkeypatch.setattr(qmod, "get_forge", lambda **k: fake_client)
        monkeypatch.setattr(qmod, "REPO", "sumipan/nexus")
        args = argparse.Namespace(
            queue_path=str(store.queue_path),
            state_path=str(store.state_path),
            lock_path=str(store.lock_path),
        )
        qmod._cmd_doctor(args)
        out = capsys.readouterr().out
        assert "dispatch_blocked" in out
        assert "1" in out

    def test_doctor_no_blocked_still_ok(self, tmp_path, monkeypatch, capsys, issuesmith_config):
        _patch_dispatch(tmp_path, monkeypatch, issuesmith_config)
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        fake_client = _FakeClient({})
        monkeypatch.setattr(qmod, "get_forge", lambda **k: fake_client)
        monkeypatch.setattr(qmod, "REPO", "sumipan/nexus")
        args = argparse.Namespace(
            queue_path=str(store.queue_path),
            state_path=str(store.state_path),
            lock_path=str(store.lock_path),
        )
        qmod._cmd_doctor(args)
        out = capsys.readouterr().out
        assert "doctor ok" in out


