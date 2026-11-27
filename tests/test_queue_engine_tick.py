"""Queue CLI halt/status, engine pause, tick API budget and issue cache."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime

import pytest

from tests import test_queue as _base
from tests.test_queue import _NOW, _ProdShapeClient, _store

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")


class TestCLI:
    def test_enqueue_cli(self, tmp_path, capsys):
        from issuesmith import queue as qmod

        code = qmod.main(
            [
                "--queue-path",
                str(tmp_path / "q.jsonl"),
                "--state-path",
                str(tmp_path / "s.json"),
                "--lock-path",
                str(tmp_path / "l.lock"),
                "enqueue",
                "--issue",
                "100",
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
            ]
        )
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["created"] is True
        line = (tmp_path / "q.jsonl").read_text(encoding="utf-8").strip()
        row = json.loads(line)
        assert row["issue"] == 100
        assert "+" in row["requested_at"] or row["requested_at"].endswith("Z") or ":" in row["requested_at"][-6:]

    def test_enqueue_cli_invalid_exit_2(self, tmp_path):
        from issuesmith import queue as qmod

        code = qmod.main(
            [
                "--queue-path",
                str(tmp_path / "q.jsonl"),
                "--state-path",
                str(tmp_path / "s.json"),
                "--lock-path",
                str(tmp_path / "l.lock"),
                "enqueue",
                "--issue",
                "0",
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
            ]
        )
        assert code == 2
        assert not (tmp_path / "q.jsonl").exists()

    def test_reset_clears_halt(self, tmp_path, capsys):
        """reset clears halt (#2808)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_halt(True, "previous issue not merge-done")

        code = qmod.main(
            [
                "--queue-path",
                str(tmp_path / "queue.jsonl"),
                "--state-path",
                str(tmp_path / "state.json"),
                "--lock-path",
                str(tmp_path / "lock"),
                "reset",
            ]
        )
        assert code == 0
        snap = store.snapshot()
        assert snap.halt is True
        out = capsys.readouterr().out
        assert "blocks unchanged" in out

# --- role-aware quota gating (2026-09-04) ---

def _write_quota(path, engines: dict[str, str]) -> None:
    import json as _json

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
    payload = {
        "schema_version": 1,
        "deferred_tasks": {},
        "draining_engines": {},
        "running_tasks": {},
        "engines": engine_payloads,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(payload), encoding="utf-8")


def _write_engine_state(path, design: str, implementation: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"design:\n  engine: {design}\n  model: m\nimplementation:\n  engine: {implementation}\n  model: m\n", encoding="utf-8")


def test_required_engines_paused_blocks_on_design_engine(tmp_path):
    from issuesmith import queue as qmod
    q = tmp_path / "quota.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "paused", "codex": "available"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e) == ["claude"]


def test_required_engines_paused_ignores_fallback_only_engine(tmp_path):
    from issuesmith import queue as qmod
    q = tmp_path / "quota.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"codex": "paused"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e) == []


def test_required_engines_paused_empty_when_no_engines(tmp_path):
    from issuesmith import queue as qmod
    q = tmp_path / "quota.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e) == []


def test_required_engines_paused_same_path_no_duplicate(tmp_path, monkeypatch):
    """AC-5: same path → one snapshot and no duplicate engine names."""
    from ghdag.quota import QuotaGate

    from issuesmith import queue as qmod

    q = tmp_path / "quota.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "paused", "cursor": "paused"})
    _write_engine_state(e, "claude", "cursor")

    snap_count = {"n": 0}
    real_snapshot = QuotaGate.snapshot

    def counting_snapshot(self, *args, **kwargs):
        snap_count["n"] += 1
        return real_snapshot(self, *args, **kwargs)

    monkeypatch.setattr(QuotaGate, "snapshot", counting_snapshot)
    assert qmod._required_engines_paused(q, e) == ["claude", "cursor"]
    assert snap_count["n"] == 1


class TestRoleScopedPausedDispatch:
    """AC-4: claude paused with resume_at=null still allows develop(cursor) (#3091)."""

    def test_status_shows_role_in_paused_wait_reason(self, tmp_path, monkeypatch, capsys):
        from issuesmith import queue as qmod

        quota_path = tmp_path / "quota.json"
        engine_path = tmp_path / "engine.yml"
        _write_quota(quota_path, {"claude": "paused"})
        _write_engine_state(engine_path, "claude", "cursor")

        store = _store(tmp_path)
        store.enqueue(
            issue=3086, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        monkeypatch.setattr(qmod, "QUOTA_STATE_PATH", quota_path)
        monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", engine_path)
        monkeypatch.setattr(
            qmod,
            "get_forge",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("offline")),
        )
        code = qmod.main(
            [
                "--queue-path",
                str(tmp_path / "queue.jsonl"),
                "--state-path",
                str(tmp_path / "state.json"),
                "--lock-path",
                str(tmp_path / "lock"),
                "status",
            ]
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "required engine paused: claude (role=design)" in out


# ---------------------------------------------------------------------------
# #3759: tick API call budget (issue_get memoization / list unification)
# ---------------------------------------------------------------------------


class _CountingClient(_ProdShapeClient):
    """_ProdShapeClient that records every forge call and honors ``labels=`` filters."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.calls: list[tuple[str, object]] = []

    def issue_get(self, number, fields=None):
        self.calls.append(("issue_get", number))
        return super().issue_get(number, fields=fields)

    def issue_update(self, number, labels_add=None, labels_remove=None):
        self.calls.append(("issue_update", number))
        super().issue_update(number, labels_add=labels_add, labels_remove=labels_remove)

    def issue_comment(self, number, body):
        self.calls.append(("issue_comment", number))
        super().issue_comment(number, body)

    def get_issue_comments(self, number):
        self.calls.append(("get_issue_comments", number))
        return super().get_issue_comments(number)

    def list_issues(self, label, state="open"):
        self.calls.append(("list_issues", label))
        return [
            {"number": n}
            for n, d in self.issues.items()
            if str(d.get("state", "OPEN")).upper() == "OPEN"
            and label in {lab["name"] for lab in d.get("labels") or []}
        ]

    def pr_list(self, **kwargs):
        self.calls.append(("pr_list", kwargs.get("state")))
        return super().pr_list(**kwargs)

    def api_request(self, path, *, method="GET", fields=None, repo=None, paginate=False):
        self.calls.append(("api_request", path))
        rows = super().api_request(
            path, method=method, fields=fields, repo=repo, paginate=paginate
        )
        m = re.search(r"[?&]labels=([^&]+)", path)
        if path.startswith("issues") and m and isinstance(rows, list):
            wanted = set(m.group(1).split(","))
            rows = [
                r for r in rows
                if wanted <= {lab["name"] for lab in r.get("labels") or []}
            ]
        return rows

    def issue_get_count(self, number: int) -> int:
        return sum(1 for name, arg in self.calls if name == "issue_get" and arg == number)


_STALE_RUNNING = list(range(3000, 3017))  # 17 stale develop-running Issues (no DAG)
_IN_FLIGHT = (3647, 3739, 3572)
_QUEUED = 3700


def _paths_body(path: str) -> str:
    return (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f'  - "{path}"\n'
        "```\n"
    )


def _tick_budget_setup(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    running = [{"name": "issuesmith:develop-running"}]
    issues: dict[int, dict] = {
        n: {"state": "OPEN", "title": f"stale {n}", "body": _paths_body(f"stale/{n}/**"),
            "labels": list(running)}
        for n in _STALE_RUNNING
    }
    for n in _IN_FLIGHT:
        issues[n] = {"state": "OPEN", "title": f"inflight {n}",
                     "body": _paths_body(f"inflight/{n}/**"), "labels": list(running)}
    issues[_QUEUED] = {"state": "OPEN", "title": "queued",
                       "body": _paths_body("queued/**"), "labels": []}

    store = _store(tmp_path)
    for n in _IN_FLIGHT:
        store.add_in_flight(
            n, "codex", role="implementation",
            allow_paths=(f"inflight/{n}/**",), target_repo="sumipan/nexus",
        )
    store.enqueue(
        issue=_QUEUED, phase="draft", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )

    # No DAG state anywhere: the 17 running-labeled Issues are stale.
    monkeypatch.setattr(qmod, "EXEC_PATH", tmp_path / "exec.jsonl")
    monkeypatch.setattr(qmod, "DONE_DIR", tmp_path / "done")
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(tmp_path))
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    monkeypatch.setattr(qmod, "_handler_key_consumed", lambda *a, **k: False)
    return store, _CountingClient(issues=issues)


def _run_tick(store, client):
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    return qmod.dispatch_one(
        now=datetime(2026, 9, 25, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        client=client,
        store=store,
        skip_seed=True,
    )


def test_audit_reports_active_order_in_completed(tmp_path, capsys):
    """Audit detects active_order intersecting completed_request_ids (#5691, #5965)."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    args = argparse.Namespace(
        queue_path=str(store.queue_path),
        state_path=str(store.state_path),
        lock_path=str(store.lock_path),
    )
    rid = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="human",
        priority="normal",
        requested_by=["x"],
        requested_at=_NOW,
    ).request_id
    with store.lock():
        state = store._load_state_unlocked()
        state["active_order"] = [rid]
        state["completed_request_ids"] = [rid]
        store._save_state_unlocked(state)
    assert qmod._cmd_audit(args) == 1
    assert "active_order intersects completed_request_ids" in capsys.readouterr().out


class TestTickApiBudget:
    def test_tick_api_calls_at_most_8(self, tmp_path, monkeypatch):
        store, client = _tick_budget_setup(tmp_path, monkeypatch)
        _run_tick(store, client)
        assert len(client.calls) <= 8, client.calls

    def test_in_flight_issue_get_once_per_tick(self, tmp_path, monkeypatch):
        store, client = _tick_budget_setup(tmp_path, monkeypatch)
        _run_tick(store, client)
        for n in _IN_FLIGHT:
            assert client.issue_get_count(n) == 1, client.calls
        assert client.issue_get_count(_QUEUED) == 1, client.calls

    def test_stale_running_issues_not_fetched(self, tmp_path, monkeypatch):
        store, client = _tick_budget_setup(tmp_path, monkeypatch)
        _run_tick(store, client)
        for n in _STALE_RUNNING:
            assert client.issue_get_count(n) == 0, client.calls
        assert not [c for c in client.calls if c[0] == "list_issues"], client.calls

    def test_open_issue_list_fetched_once(self, tmp_path, monkeypatch):
        store, client = _tick_budget_setup(tmp_path, monkeypatch)
        _run_tick(store, client)
        lists = [c for c in client.calls if c[0] == "api_request" and c[1].startswith("issues")]
        assert len(lists) == 1, client.calls

    def test_in_flight_release_unchanged_with_cache(self, tmp_path, monkeypatch):
        """Terminal in_flight is still released when fetched through the tick cache."""
        store, client = _tick_budget_setup(tmp_path, monkeypatch)
        client.issues[_IN_FLIGHT[1]]["state"] = "CLOSED"
        client.issues[_IN_FLIGHT[1]]["labels"] = [{"name": "issuesmith:merge-done"}]
        _run_tick(store, client)
        remaining = {e["issue"] for e in store.snapshot().in_flight}
        assert _IN_FLIGHT[1] not in remaining
        assert {_IN_FLIGHT[0], _IN_FLIGHT[2]} <= remaining
        assert client.issue_get_count(_IN_FLIGHT[1]) == 1, client.calls


