"""tests/tools/issuesmith/test_queue.py — QueueStore comprehensive tests."""
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
from issuesmith.queue_store import (
    QueueStore,
    QueueValidationError,
    validate_request_fields,
)
from tests.conftest import _write_nexus_config

_NOW = "2026-01-01T12:00:00+00:00"


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


@pytest.fixture
def nexus_config(monkeypatch, tmp_path):
    """Nexus declaration for modules split out of this file; conftest applies it by module name."""
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(_write_nexus_config(tmp_path)))
    reset_config_cache()


class TestEnqueue:

    def test_enqueue_duplicate_merges(self, tmp_path):
        store = _store(tmp_path)
        r1 = store.enqueue(
            issue=100, phase="draft", source="test", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        r2 = store.enqueue(
            issue=100, phase="draft", source="test2", actor_kind="human",
            priority="high", requested_by=["bob"], requested_at=_NOW,
        )
        assert r1.created is True
        assert r2.merged is True
        assert r1.request_id == r2.request_id

        snap = store.snapshot()
        assert len(snap.active_order) == 1
        eff = store.effective_request(snap, r1.request_id)
        assert "bob" in eff.requested_by
        assert eff.priority == "high"


    def test_enqueue_force_flag(self, tmp_path):
        store = _store(tmp_path)
        r = store.enqueue(
            issue=200, phase="merge", source="recovery", actor_kind="human",
            priority="high", requested_by=["admin"], requested_at=_NOW,
            force=True,
        )
        snap = store.snapshot()
        assert store.is_force(snap, r.request_id) is True


class TestSnapshot:
    def test_empty_snapshot(self, tmp_path):
        store = _store(tmp_path)
        snap = store.snapshot()
        assert snap.active_order == []
        assert snap.halt is False


class TestValidation:
    def test_invalid_issue(self):
        with pytest.raises(QueueValidationError):
            validate_request_fields(
                issue=0, phase="draft", source="s", actor_kind="human",
                priority="normal", requested_at=_NOW, requested_by=["a"],
            )

    def test_invalid_phase(self):
        with pytest.raises(QueueValidationError):
            validate_request_fields(
                issue=1, phase="invalid", source="s", actor_kind="human",
                priority="normal", requested_at=_NOW, requested_by=["a"],
            )

    def test_empty_requested_by(self):
        with pytest.raises(QueueValidationError):
            validate_request_fields(
                issue=1, phase="draft", source="s", actor_kind="human",
                priority="normal", requested_at=_NOW, requested_by=[],
            )

    def test_invalid_actor_kind(self):
        with pytest.raises(QueueValidationError):
            validate_request_fields(
                issue=1, phase="draft", source="s", actor_kind="bot",
                priority="normal", requested_at=_NOW, requested_by=["a"],
            )

    def test_naive_datetime_rejected(self):
        with pytest.raises(QueueValidationError, match="timezone"):
            validate_request_fields(
                issue=1, phase="draft", source="s", actor_kind="human",
                priority="normal", requested_at="2026-09-03T12:00:00", requested_by=["a"],
            )

    def test_unknown_fields_rejected(self):
        from issuesmith.queue_store import request_from_dict

        with pytest.raises(QueueValidationError, match="unknown fields"):
            request_from_dict(
                {
                    "request_id": "00000000-0000-4000-8000-000000000001",
                    "issue": 1,
                    "phase": "draft",
                    "source": "s",
                    "actor_kind": "human",
                    "priority": "normal",
                    "requested_at": _NOW,
                    "requested_by": ["a"],
                    "extra_field": "nope",
                }
            )


class TestSeedKey:
    def test_seed_key_idempotent(self, tmp_path):
        store = _store(tmp_path)
        r1 = store.enqueue(
            issue=10, phase="draft", source="seed", actor_kind="automation",
            priority="low", requested_by=["nq"], requested_at=_NOW,
            seed_key="nq:10:draft",
        )
        r2 = store.enqueue(
            issue=10, phase="draft", source="seed", actor_kind="automation",
            priority="low", requested_by=["nq"], requested_at=_NOW,
            seed_key="nq:10:draft",
        )
        assert r1.created is True
        assert r2.created is False
        snap = store.snapshot()
        assert len(snap.active_order) == 1


_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)


class TestDeterministicDecision:
    def test_closed(self):
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "draft", "s",
            "automation", "low", _NOW, ("bot",),
        )
        d = deterministic_decision(req, {"state": "CLOSED", "title": "x", "body": _VALID_BODY, "labels": []})
        assert d.kind == "closed"
        assert d.close_issue is False

    def test_closed_force_without_done_keeps(self):
        """CLOSED + force without the requested phase's done label → keep (#2825 issue 2)."""
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "merge", "recovery",
            "human", "high", _NOW, ("sumipan",),
        )
        d = deterministic_decision(
            req,
            {
                "state": "CLOSED",
                "title": "x",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:develop-done"}],
            },
            force=True,
        )
        assert d.kind == "keep"


    def test_already_ready(self):
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "draft", "s",
            "automation", "low", _NOW, ("bot",),
        )
        d = deterministic_decision(
            req,
            {
                "state": "OPEN",
                "title": "x",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:draft-ready"}],
            },
        )
        assert d.kind == "already_processed"
        assert d.comment is False

    def test_done_label_blocks_without_force(self):
        """Re-enqueue develop on a develop-done Issue is blocked without force (2026-09-04)."""
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "develop", "recovery",
            "human", "high", _NOW, ("sumipan",),
        )
        d = deterministic_decision(
            req,
            {
                "state": "OPEN",
                "title": "x",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:develop-done"}],
            },
        )
        assert d.kind == "already_processed"


    def test_running_label_blocks_even_with_force(self):
        """force=True must not bypass -running (in progress) — keep #2813 double-enqueue protection."""
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "develop", "recovery",
            "human", "high", _NOW, ("sumipan",),
        )
        d = deterministic_decision(
            req,
            {
                "state": "OPEN",
                "title": "x",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:develop-running"}],
            },
            force=True,
        )
        assert d.kind == "already_processed"


    def test_newer_bump_title_does_not_close_request(self):
        """Refs #7329."""
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 10, "draft", "s",
            "automation", "low", _NOW, ("bot",),
        )
        d = deterministic_decision(
            req,
            {
                "state": "OPEN",
                "number": 10,
                "title": "app: bump dep to v0.1.0",
                "body": _VALID_BODY,
                "labels": [],
            },
        )
        assert d.kind == "keep"
        assert d.close_issue is False


class TestNightSourceWindow:
    def test_extra_sources_gate_automation_only_in_window(self):
        """Refs #7329."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        now_in = datetime(2026, 9, 3, 3, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        now_out = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        extra = frozenset({"watcher"})
        assert qmod._source_in_window(
            "watcher", "automation", now_in, "01:00", "07:00", extra
        )
        assert not qmod._source_in_window(
            "watcher", "automation", now_out, "01:00", "07:00", extra
        )
        assert qmod._source_in_window(
            "watcher", "automation", now_out, "01:00", "07:00", frozenset()
        )
        assert qmod._source_in_window(
            "skill", "human", now_out, "01:00", "07:00", extra
        )


# ---------------------------------------------------------------------------
# CP2 regression: production GitHubClient response shapes
# ---------------------------------------------------------------------------


class _ProdShapeClient:
    """Fake client matching production GitHubClient contracts.

    - pr_list: normalized PRs WITHOUT body (search filters title/head only)
    - pr_get: includes body
    - api_request: raw open issues (includes PRs with pull_request key)
    - NO list_open_issues_for_queue (that helper does not exist in production)
    """

    def __init__(
        self,
        *,
        issues: dict[int, dict] | None = None,
        open_prs: list[dict] | None = None,
        closed_prs: list[dict] | None = None,
        open_issue_rows: list[dict] | None = None,
    ):
        self.issues = issues or {}
        self.open_prs = open_prs or []
        self.closed_prs = closed_prs or []
        self.open_issue_rows = open_issue_rows
        self.updates: list[tuple] = []
        self.comments: list[tuple] = []
        self.closes: list[int] = []
        self.pr_get_calls: list[int] = []
        self.api_request_calls: list[str] = []

    def issue_get(self, number, fields=None):
        data = dict(self.issues[number])
        data.setdefault("number", number)
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
        self.updates.append((number, labels_add, labels_remove))

    def issue_comment(self, number, body):
        self.comments.append((number, body))

    def issue_close(self, number):
        self.closes.append(number)

    def get_issue_comments(self, number):
        return []

    def list_issues(self, label, state="open"):
        return []

    def _all_prs(self):
        return list(self.open_prs) + list(self.closed_prs)

    def pr_list(self, *, head=None, state=None, search=None, repo=None, limit=30):
        # Production: search matches title/head only; body is stripped.
        state_u = (state or "open").upper()
        source = self.open_prs if state_u == "OPEN" else self.closed_prs
        if state_u in ("ALL", ""):
            source = self._all_prs()
        items = []
        for pr in source:
            item = {
                "number": pr["number"],
                "title": pr.get("title") or "",
                "state": (pr.get("state") or "OPEN").upper(),
                "url": pr.get("url"),
                "headRefName": pr.get("headRefName") or "",
            }
            # Preserve merge metadata when the fixture provides it (list may omit body).
            for key in ("mergedAt", "merged_at", "merged"):
                if key in pr:
                    item[key] = pr[key]
            if search:
                q = search.lower()
                if q not in item["title"].lower() and q not in item["headRefName"].lower():
                    continue
            items.append(item)
        return items[:limit]

    def pr_get(self, number, *, repo=None):
        self.pr_get_calls.append(number)
        for pr in self._all_prs():
            if pr["number"] == number:
                out = {
                    "number": number,
                    "title": pr.get("title") or "",
                    "body": pr.get("body") or "",
                    "state": (pr.get("state") or "OPEN").upper(),
                    "url": pr.get("url"),
                }
                for key in ("mergedAt", "merged_at", "merged"):
                    if key in pr:
                        out[key] = pr[key]
                return out
        raise RuntimeError(f"PR #{number} not found")

    def api_request(self, path, *, method="GET", fields=None, repo=None, paginate=False):
        self.api_request_calls.append(path)
        if path.startswith("issues"):
            if self.open_issue_rows is not None:
                return list(self.open_issue_rows)
            return [
                {
                    "number": n,
                    "title": d.get("title") or "",
                    "state": (d.get("state") or "open").lower(),
                    "body": d.get("body") or "",
                    "labels": d.get("labels") or [],
                }
                for n, d in self.issues.items()
                if str(d.get("state", "OPEN")).upper() == "OPEN"
            ]
        if path.startswith("pulls/"):
            # Raw pull detail (includes merged_at) — production pr_get strips it.
            try:
                num = int(path.split("/", 1)[1].split("?", 1)[0])
            except ValueError:
                return {}
            for pr in self._all_prs():
                if pr["number"] == num:
                    return {
                        "number": num,
                        "title": pr.get("title") or "",
                        "body": pr.get("body") or "",
                        "state": (pr.get("state") or "closed").lower(),
                        "merged_at": pr.get("merged_at") or pr.get("mergedAt"),
                        "merged": pr.get("merged", bool(pr.get("merged_at") or pr.get("mergedAt"))),
                    }
            return {}
        if path.startswith("pulls"):
            # Raw REST list: filtered by state, keeps body and merged_at.
            source = self.closed_prs if "state=closed" in path else self.open_prs
            return [
                {
                    **pr,
                    "state": (pr.get("state") or "open").lower(),
                    "merged_at": pr.get("merged_at") or pr.get("mergedAt"),
                }
                for pr in source
            ]
        return []


# ---------------------------------------------------------------------------
# #7963: legacy enqueue --after / request_meta.after removal
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_PATH = _REPO_ROOT / "src"
_QUEUE_AFTER_NOW = datetime(2026, 9, 22, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")).isoformat()
_JST = ZoneInfo("Asia/Tokyo")

_AFTER_FEATURE_PATTERNS = (
    re.compile(r"after_deps"),
    re.compile(r'get\("after"\)'),
    re.compile(r'\.get\("after"\)'),
    re.compile(r"add_argument\(\"--after\""),
    re.compile(r"dispatch_blocked"),
    re.compile(r"after:\s*list\[int\]"),
)


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


def _pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH", "")
    return str(_SRC_PATH) + (f":{existing}" if existing else "")


def _run_queue(*args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    run_env = {**os.environ, "PYTHONPATH": _pythonpath()}
    if env:
        run_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", "queue", *args],
        cwd=str(_REPO_ROOT),
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


class _QueueAfterFakeClient:
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
        requested_at=_QUEUE_AFTER_NOW,
    )
    with store.lock():
        state = store._load_state_unlocked()
        meta = dict(state.get("request_meta") or {})
        meta[result.request_id] = {"after": [99]}
        state["request_meta"] = meta
        store._save_state_unlocked(state)

    client = _QueueAfterFakeClient(
        {50: {"state": "OPEN", "labels": [], "body": _VALID_BODY, "title": "main"}}
    )
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
        requested_at=_QUEUE_AFTER_NOW,
    )
    with store.lock():
        state = store._load_state_unlocked()
        meta = dict(state.get("request_meta") or {})
        meta[result.request_id] = {"after": [200, 300]}
        state["request_meta"] = meta
        store._save_state_unlocked(state)

    monkeypatch.setattr(
        qmod, "get_forge", lambda **k: _QueueAfterFakeClient({50: {"body": _VALID_BODY}})
    )
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
        text = (_SRC_PATH / rel).read_text(encoding="utf-8")
        for pattern in _AFTER_FEATURE_PATTERNS:
            for match in pattern.finditer(text):
                hits.append(f"{rel}:{match.group(0)}")
    assert hits == []
