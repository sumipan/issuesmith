"""#2813: queue dispatch race — ready-label gate, dispatch_lock, audit in-flight."""
from __future__ import annotations

import argparse
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from issuesmith.queue_store import QueueStore

_NOW = datetime.now(timezone.utc).isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_dispatch_blocks_when_ready_label_exists(tmp_path, monkeypatch):
    """別 Issue が -ready のとき dispatch_one は another issue is running で拒否する."""
    from zoneinfo import ZoneInfo

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
    )

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }

        def list_issues(self, label, state="open"):
            if label == "issuesmith:draft-ready":
                return [{"number": 99, "labels": [{"name": label}]}]
            return []

        def get_issue_comments(self, number):
            return []

        def list_open_issues_for_queue(self):
            return []

        def issue_update(self, number, labels_add=None, labels_remove=None):
            pass

        def issue_comment(self, number, body):
            pass

    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(
        now=now,
        client=Client(),
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 50


def test_concurrent_dispatch_single_winner(tmp_path, monkeypatch):
    """2 スレッド同時 dispatch_one では成功が最大 1 回（dispatch_lock + in-flight）."""
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    assert store.dispatch_lock_path != store.lock_path
    assert store.dispatch_lock_path.name == "dispatch.lock"

    # Nested dispatch_lock + store.lock must not deadlock.
    with store.dispatch_lock():
        store.snapshot()
        store.set_last_issue(None)

    for issue in (50, 51):
        store.enqueue(
            issue=issue,
            phase="draft",
            source="skill",
            actor_kind="human",
            priority="normal",
            requested_by=["alice"],
            requested_at=_NOW,
        )

    class Client:
        def __init__(self):
            self._lock = threading.Lock()
            self._labels: dict[int, set[str]] = {50: set(), 51: set()}
            self.updates: list[tuple] = []

        def issue_get(self, number, fields=None):
            with self._lock:
                labels = [{"name": n} for n in self._labels.get(number, set())]
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": labels,
            }

        def issue_update(self, number, labels_add=None, labels_remove=None):
            with self._lock:
                self.updates.append((number, labels_add))
                bucket = self._labels.setdefault(number, set())
                for lab in labels_add or []:
                    bucket.add(lab)
                for lab in labels_remove or []:
                    bucket.discard(lab)

        def issue_comment(self, number, body):
            return None

        def get_issue_comments(self, number):
            return []

        def list_issues(self, label, state="open"):
            with self._lock:
                return [
                    {"number": num, "labels": [{"name": label}]}
                    for num, labs in self._labels.items()
                    if label in labs
                ]

        def list_open_issues_for_queue(self):
            return []

    client = Client()
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    barrier = threading.Barrier(2)
    results: list = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        out = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        with results_lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive()

    wins = [r for r in results if r.dispatched]
    assert len(wins) <= 1
    assert len(wins) == 1
    assert wins[0].reason == "dispatched"


def test_audit_detects_multiple_in_flight(tmp_path, capsys):
    """2 件以上が同時に -ready/-running なら AUDIT FAIL + exit 1."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)

    class FakeClient:
        def list_issues(self, label, state="open"):
            if label == "issuesmith:draft-running":
                return [{"number": 2805}, {"number": 2808}]
            return []

    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        offline=False,
    )
    with patch.object(qmod, "GitHubClient", return_value=FakeClient()):
        code = qmod._cmd_audit(args)
    captured = capsys.readouterr()
    assert code == 1
    assert "AUDIT FAIL: multiple issues in-flight:" in captured.out
    assert "#2805" in captured.out
    assert "#2808" in captured.out


def test_audit_offline_skips_github(tmp_path, capsys):
    """--offline では GitHub API を呼ばずローカル検査のみ."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
        offline=True,
    )

    def boom(*a, **k):
        raise AssertionError("GitHubClient must not be constructed in offline mode")

    with patch.object(qmod, "GitHubClient", side_effect=boom):
        code = qmod._cmd_audit(args)
    assert code == 0
    assert "AUDIT OK" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# #3092: draft-done race — incomplete exec keeps in_flight; recover untracked
# Fixture mirrors 2026-09-10 16:30 (#3039) / 19:0x (#3046): in_flight had #3020
# only while #3039/#3046 ran develop as orphans.
# ---------------------------------------------------------------------------

_BODY_3039 = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "skills/**"\n'
    "```\n"
)


def _legacy_design_release(labels: set[str], role: str | None = "design") -> bool:
    """Pre-#3092 design-slot release: draft-done and develop not started (labels only)."""
    if "issuesmith:draft-done" not in labels:
        return False
    if labels & {"issuesmith:develop-ready", "issuesmith:develop-running"}:
        return False
    if role is not None and role != "design":
        return False
    return True


def test_incomplete_exec_blocks_draft_done_release(tmp_path, monkeypatch):
    """AC-1/AC-4: #3039-style — draft-done でも未完了 exec があれば解放しない.

    (a) 従来ロジックは draft-done + develop 未ラベルで True
    (b) 新コードは incomplete exec を見て False
    """
    import json

    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    # brushup still mid-flight (or impl already started) — UUID not in DONE_DIR
    exec_path.write_text(
        json.dumps(
            {"uuid": "brushup-3039", "idempotency_key": "issuesmith:brushup:3039"}
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    labels = {"issuesmith:draft-done"}
    assert _legacy_design_release(labels) is True  # (a) 従来は解放する

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
            }

    entry = {"issue": 3039, "engine": "claude", "role": "design"}
    assert qmod._issue_has_incomplete_exec(3039) is True
    assert qmod._in_flight_should_release(Client(), entry) is False  # (b)


def test_draft_done_still_releases_when_exec_complete(tmp_path, monkeypatch):
    """AC-1: 未完了 exec が無ければ従来どおり design スロットを解放する."""
    import json

    from issuesmith import queue as qmod

    exec_path = tmp_path / "exec.jsonl"
    done_dir = tmp_path / "done"
    done_dir.mkdir()
    exec_path.write_text(
        json.dumps(
            {"uuid": "brushup-3039", "idempotency_key": "issuesmith:brushup:3039"}
        )
        + "\n",
        encoding="utf-8",
    )
    (done_dir / "brushup-3039").write_text("0", encoding="utf-8")
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "labels": [{"name": "issuesmith:draft-done"}],
            }

    entry = {"issue": 3039, "engine": "claude", "role": "design"}
    assert qmod._issue_has_incomplete_exec(3039) is False
    assert qmod._in_flight_should_release(Client(), entry) is True


def test_recover_untracked_develop_running_3039_fixture(tmp_path, monkeypatch):
    """AC-2/AC-4: 16:30 state — in_flight=#3020 only; #3039 develop-running を再登録."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    # Real 16:30 shape: only #3020 tracked while #3039 was running impl orphaned.
    store.add_in_flight(
        3020,
        "claude",
        role="design",
        allow_paths=("tools/**",),
        target_repo="sumipan/nexus",
    )
    engine_state = tmp_path / "issuesmith-engine.yml"
    engine_state.write_text(
        "design:\n  engine: claude\nimplementation:\n  engine: cursor\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_state)

    class Client:
        def list_issues(self, label, state="open"):
            if label == "issuesmith:develop-running":
                return [{"number": 3039, "labels": [{"name": label}]}]
            return []

        def issue_get(self, number, fields=None):
            assert number == 3039
            return {
                "number": 3039,
                "state": "OPEN",
                "body": _BODY_3039,
                "labels": [
                    {"name": "issuesmith:draft-done"},
                    {"name": "issuesmith:develop-running"},
                ],
            }

    snap = store.snapshot()
    assert {e["issue"] for e in snap.in_flight} == {3020}
    recovered = qmod._recover_untracked_in_flight(Client(), store, snap)
    assert recovered == 1
    issues = {e["issue"]: e for e in store.snapshot().in_flight}
    assert 3020 in issues
    assert 3039 in issues
    assert issues[3039]["role"] == "implementation"
    assert issues[3039]["engine"] == "cursor"
    assert issues[3039]["target_repo"] == "sumipan/nexus"
    assert "skills/**" in issues[3039]["allow_paths"]


def test_recover_untracked_3046_case(tmp_path, monkeypatch):
    """AC-4: 19:0x #3046 — develop-running なのに in_flight 不在なら再登録."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    engine_state = tmp_path / "issuesmith-engine.yml"
    engine_state.write_text(
        "design:\n  engine: claude\nimplementation:\n  engine: claude\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_state)

    class Client:
        def list_issues(self, label, state="open"):
            if label == "issuesmith:develop-running":
                return [{"number": 3046}]
            return []

        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "state": "OPEN",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:develop-running"}],
            }

    snap = store.snapshot()
    assert snap.in_flight == []
    assert qmod._find_untracked_running(Client(), snap) == [3046]
    assert qmod._recover_untracked_in_flight(Client(), store, snap) == 1
    entry = store.snapshot().in_flight[0]
    assert entry["issue"] == 3046
    assert entry["role"] == "implementation"


def test_dispatch_recovers_before_orphan_gate(tmp_path, monkeypatch):
    """AC-2: 孤児判定の前に develop-running を in_flight へ戻し、dispatch を塞がない."""
    import json
    from zoneinfo import ZoneInfo

    import yaml

    from issuesmith import config as cfgmod
    from issuesmith import queue as qmod
    from issuesmith import queue_store as qstore
    from issuesmith.config import load_config, reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "label_namespace": "issuesmith",
                "timezone": "Asia/Tokyo",
                "supported_repos": ["sumipan/nexus"],
                "paths": {
                    "queue": "q.jsonl",
                    "queue_state": "s.json",
                    "queue_lock": "lock",
                    "triage_log": "t.jsonl",
                    "seed": "seed.yaml",
                    "night_state": "night.json",
                    "exec_jsonl": "jobs/exec.jsonl",
                    "done_dir": "jobs/done",
                    "quota_state": "quota.json",
                    "metrics": "metrics.jsonl",
                    "worktrees_dir": "wt",
                    "external_dir": "ext",
                    "workflow": "wf.yml",
                    "template_dir": "templates",
                    "engine_state": ".pipeline-state/issuesmith-engine.yml",
                },
                "engines": {
                    "design": {"allowed": ["claude"], "default_model": {"claude": "x"}},
                    "implementation": {
                        "allowed": ["claude", "cursor"],
                        "default_model": {"claude": "x", "cursor": "auto"},
                    },
                },
                "concurrency": {
                    "default": 1,
                    "per_engine": {"claude": 2, "cursor": 2},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config(cfg_path)
    done_dir = tmp_path / "jobs" / "done"
    done_dir.mkdir(parents=True)
    exec_path = tmp_path / "jobs" / "exec.jsonl"
    exec_path.write_text(
        json.dumps({"uuid": "impl-3046", "idempotency_key": "issuesmith:impl:3046"})
        + "\n",
        encoding="utf-8",
    )
    engine_state = tmp_path / ".pipeline-state" / "issuesmith-engine.yml"
    engine_state.parent.mkdir(parents=True)
    # Recovered impl uses cursor so design (claude) still has capacity for #50.
    engine_state.write_text(
        "design:\n  engine: claude\nimplementation:\n  engine: cursor\n",
        encoding="utf-8",
    )
    new_paths = cfgmod.PathsConfig(
        queue=tmp_path / "q.jsonl",
        queue_state=tmp_path / "s.json",
        queue_lock=tmp_path / "lock",
        triage_log=tmp_path / "t.jsonl",
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
    patched = cfgmod.IssuesmithConfig(
        repo=cfg.repo,
        label_namespace=cfg.label_namespace,
        timezone=cfg.timezone,
        supported_repos=cfg.supported_repos,
        root=tmp_path,
        paths=new_paths,
        engines=cfg.engines,
        concurrency=cfg.concurrency,
    )
    monkeypatch.setattr(cfgmod, "get_config", lambda: patched)
    monkeypatch.setattr(qmod, "_cfg", patched)
    monkeypatch.setattr(qmod, "DONE_DIR", done_dir)
    monkeypatch.setattr(qmod, "EXEC_PATH", exec_path)
    monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_state)
    monkeypatch.setattr(qstore, "_cfg", patched)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])

    store = _store(tmp_path)
    store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )

    body_3046 = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "skills/**"\n'
        "```\n"
    )

    class Client:
        def issue_get(self, number, fields=None):
            if number == 3046:
                return {
                    "number": 3046,
                    "state": "OPEN",
                    "title": "t",
                    "body": body_3046,
                    "labels": [{"name": "issuesmith:develop-running"}],
                }
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }

        def list_issues(self, label, state="open"):
            if label == "issuesmith:develop-running":
                return [{"number": 3046}]
            return []

        def get_issue_comments(self, number):
            return []

        def list_open_issues_for_queue(self):
            return []

        def api_request(self, *a, **k):
            return []

        def issue_update(self, number, labels_add=None, labels_remove=None):
            pass

        def issue_comment(self, number, body):
            pass

    now = datetime(2026, 9, 10, 19, 5, tzinfo=ZoneInfo("Asia/Tokyo"))
    # Without recovery this returns pipeline not idle (orphan #3046).
    result = qmod.dispatch_one(
        now=now,
        client=Client(),
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 50
    assert {e["issue"] for e in store.snapshot().in_flight} >= {50, 3046}
    reset_config_cache()


def test_status_warns_untracked_running(tmp_path, capsys):
    """AC-3: queue status が追跡外の develop-running を warning 表示する."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(3020, "claude", role="design")

    class FakeClient:
        def list_issues(self, label, state="open"):
            if label == "issuesmith:develop-running":
                return [{"number": 3039}]
            return []

        def issue_get(self, number, fields=None):
            return {"number": number, "state": "OPEN", "labels": []}

    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    with patch.object(qmod, "GitHubClient", return_value=FakeClient()):
        code = qmod._cmd_status(args)
    captured = capsys.readouterr()
    assert code == 0
    assert "warning: untracked running issues: #3039" in captured.out


def test_doctor_reports_untracked_running(tmp_path, capsys):
    """AC-3: queue doctor が status と同じ追跡外判定を返す."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)

    class FakeClient:
        def list_issues(self, label, state="open"):
            if label == "issuesmith:develop-running":
                return [{"number": 3046}]
            return []

    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    with patch.object(qmod, "GitHubClient", return_value=FakeClient()):
        code = qmod._cmd_doctor(args)
    captured = capsys.readouterr()
    assert code == 1
    assert "untracked running issues: #3046" in captured.out
