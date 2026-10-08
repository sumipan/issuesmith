"""Release in_flight for CLOSED issues without terminal labels when DAG is not live (#4910)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import load_config, reset_config_cache
from issuesmith.observe.dag_state import DagState
from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()
_JST = ZoneInfo("Asia/Tokyo")
_BODY_TESTS = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tests/**"\n'
    "```\n"
)


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
    return exec_path, done_dir


class _ListIssuesClient:
    def __init__(self, issues: dict[int, dict]) -> None:
        self.issues = issues

    def issue_get(self, number: int, fields=None):
        data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        return data

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        return []


def _dag_states_for(issue: int, status: str) -> dict[int, DagState]:
    key = f"issuesmith:impl:{issue}"
    return {issue: DagState(issue=issue, key=key, status=status)}


def _closed_draft_done(issue: int, extra_labels: list[str] | None = None) -> dict:
    labels = [{"name": "issuesmith:draft-done"}]
    for lab in extra_labels or []:
        labels.append({"name": lab})
    return {"state": "CLOSED", "labels": labels, "body": _BODY_TESTS}


def test_release_closed_draft_done_when_dag_succeeded(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        4819,
        "cursor",
        role="implementation",
        phase="develop",
        allow_paths=("tests/**",),
        target_repo="sumipan/issuesmith",
    )
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})

    def fake_load(*_a, **_k):
        return _dag_states_for(4819, "succeeded")

    monkeypatch.setattr("issuesmith.observe.dag_state.load_dag_states", fake_load)
    qmod._release_finished_in_flight(store, client, [])
    assert store.snapshot().in_flight == []


def test_release_closed_draft_done_when_no_dag_entry(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(4819, "cursor", role="implementation", phase="develop")
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})
    monkeypatch.setattr("issuesmith.observe.dag_state.load_dag_states", lambda *_a, **_k: {})
    qmod._release_finished_in_flight(store, client, [])
    assert store.snapshot().in_flight == []


@pytest.mark.parametrize("status", ["failed", "deferred"])
def test_release_closed_with_stale_running_label_when_dag_terminal(
    tmp_path, monkeypatch, status
):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(4819, "cursor", role="implementation", phase="develop")
    client = _ListIssuesClient(
        {4819: _closed_draft_done(4819, extra_labels=["issuesmith:develop-running"])}
    )
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, status),
    )
    qmod._release_finished_in_flight(store, client, [])
    assert store.snapshot().in_flight == []


def test_closed_with_running_dag_not_released(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, "running"),
    )
    assert qmod._in_flight_should_release(client, entry) is False


def test_closed_merge_running_pending_gap_not_released(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient(
        {4819: _closed_draft_done(4819, extra_labels=["issuesmith:merge-running"])}
    )
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, "pending"),
    )
    assert qmod._in_flight_should_release(client, entry) is False


def test_load_dag_states_error_fail_closed(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})

    def boom(*_a, **_k):
        raise OSError("unreadable")

    monkeypatch.setattr("issuesmith.observe.dag_state.load_dag_states", boom)
    assert qmod._in_flight_should_release(client, entry) is False


def test_open_andon_blocked_not_released(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    entry = {"issue": 4816, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient(
        {
            4816: {
                "state": "OPEN",
                "labels": [
                    {"name": "issuesmith:andon-blocked"},
                    {"name": "issuesmith:develop-running"},
                ],
            }
        }
    )
    monkeypatch.setattr("issuesmith.observe.dag_state.load_dag_states", lambda *_a, **_k: {})
    assert qmod._in_flight_should_release(client, entry) is False


def test_closed_terminal_label_releases_without_stderr_log(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient(
        {
            4819: {
                "state": "CLOSED",
                "labels": [{"name": "issuesmith:merge-done"}],
            }
        }
    )
    assert qmod._in_flight_should_release(client, entry) is True
    assert "closed without terminal label" not in capsys.readouterr().err


def test_closed_non_terminal_release_logs_stderr(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, "succeeded"),
    )
    assert qmod._in_flight_should_release(client, entry) is True
    assert capsys.readouterr().err.strip() == (
        "[queue] in_flight released: #4819 closed without terminal label"
    )


class _DispatchClient:
    def __init__(self, issues: dict[int, dict]) -> None:
        self.issues = issues
        self.updates: list[tuple[int, list[str] | None]] = []

    def issue_get(self, number, fields=None):
        data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        data.setdefault("title", "t")
        data.setdefault("body", _BODY_TESTS)
        data.setdefault("labels", [])
        data.setdefault("state", "OPEN")
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
        self.updates.append((number, labels_add))
        bucket = self.issues.setdefault(number, {"labels": []})
        names = {
            lab["name"] if isinstance(lab, dict) else lab for lab in bucket.get("labels", [])
        }
        for lab in labels_add or []:
            names.add(lab)
        bucket["labels"] = [{"name": n} for n in sorted(names)]

    def issue_comment(self, number, body):
        pass

    def get_issue_comments(self, number):
        return []


def test_dispatch_after_closed_in_flight_released_same_tick(
    tmp_path, monkeypatch, issuesmith_config
):
    _patch_paths(tmp_path, monkeypatch, issuesmith_config)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(
        4819,
        "cursor",
        role="implementation",
        phase="develop",
        allow_paths=("tests/**",),
        target_repo="sumipan/issuesmith",
    )
    store.enqueue(
        issue=4874,
        phase="develop",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    client = _DispatchClient(
        {
            4819: _closed_draft_done(4819),
            4874: {
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
                "body": _BODY_TESTS,
            },
        }
    )
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, "succeeded"),
    )
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 10, 7, 12, 0, tzinfo=_JST)
    result = qmod.dispatch_one(now=now, client=client, store=store, skip_seed=True)
    assert result.dispatched is True
    assert result.issue == 4874
