"""Queue CLI halt/status, engine pause, tick API budget and issue cache."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from tests import test_queue as _base
from tests.test_queue import _NOW, _VALID_BODY, _ProdShapeClient, _store

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

    def test_status_shows_halt_reason(self, tmp_path, capsys):
        """When blocks are active, status lists them (#2808)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_halt(True, "pipeline idle timeout")
        store.enqueue(
            issue=2808, phase="draft", source="night-queue", actor_kind="automation",
            priority="normal", requested_by=["nq"], requested_at=_NOW,
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
        assert "blocks=1" in out
        assert "pipeline idle timeout" in out
        assert "issue=#2808" in out


# --- role-aware quota gating (2026-09-04) ---

def _write_quota(path, engines: dict[str, str]) -> None:
    import json as _json
    payload = {
        "schema_version": 1, "deferred_tasks": {}, "draining_engines": {}, "running_tasks": {},
        "engines": {name: {"status": st, "observed_at": "2026-09-04T00:00:00+00:00", "reason": "t", "resume_at": None}
                    for name, st in engines.items()},
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


def test_required_engines_defaults_when_state_missing(tmp_path):
    from issuesmith import queue as qmod
    q = tmp_path / "quota.json"
    _write_quota(q, {"claude": "paused"})
    assert qmod._required_engines_paused(q, tmp_path / "missing.yml") == ["claude"]


def test_required_engines_paused_role_filters_to_phase_engine(tmp_path):
    """With role=, only look at that role's engine (#3091)."""
    from issuesmith import queue as qmod

    q = tmp_path / "quota.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "paused", "cursor": "available"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e, role="design") == ["claude"]
    assert qmod._required_engines_paused(q, e, role="implementation") == []


def test_required_engines_paused_brake_only(tmp_path, capsys, monkeypatch):
    """Brake-only pause is ignored; quota state alone drives engine_paused waits."""
    from issuesmith import queue as qmod

    monkeypatch.setattr(qmod, "_BRAKE_PATH_DEPRECATED_WARNED", False)
    q = tmp_path / "quota.json"
    b = tmp_path / "brake.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "available", "cursor": "available"})
    _write_quota(b, {"claude": "paused", "cursor": "available"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e, role="design", brake_path=b) == []
    assert qmod._required_engines_paused(q, e, role="implementation", brake_path=b) == []
    assert "brake_state is deprecated" in capsys.readouterr().err


def test_required_engines_paused_quota_only(tmp_path):
    """AC-4: paused on global quota gate alone is also a stop target."""
    from issuesmith import queue as qmod

    q = tmp_path / "quota.json"
    b = tmp_path / "brake.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "paused", "cursor": "available"})
    _write_quota(b, {"claude": "available", "cursor": "available"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e, role="design", brake_path=b) == ["claude"]


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
    assert qmod._required_engines_paused(q, e, brake_path=q) == ["claude", "cursor"]
    assert snap_count["n"] == 1


def test_required_engines_paused_union_dedupes(tmp_path):
    """Same engine paused on both gates still returns once."""
    from issuesmith import queue as qmod

    q = tmp_path / "quota.json"
    b = tmp_path / "brake.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "paused"})
    _write_quota(b, {"claude": "paused"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e, brake_path=b) == ["claude"]


def test_required_engines_paused_both_available(tmp_path):
    """Available on both gates → empty list."""
    from issuesmith import queue as qmod

    q = tmp_path / "quota.json"
    b = tmp_path / "brake.json"
    e = tmp_path / "engine.yml"
    _write_quota(q, {"claude": "available"})
    _write_quota(b, {"claude": "available"})
    _write_engine_state(e, "claude", "cursor")
    assert qmod._required_engines_paused(q, e, role="design", brake_path=b) == []


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_QUOTA_CLAUDE_PAUSED = _FIXTURES / "quota_claude_paused_no_resume.json"
_ENGINE_DESIGN_CLAUDE = _FIXTURES / "engine_state_design_claude_impl_cursor.yml"


class TestRoleScopedPausedDispatch:
    """AC-4: claude paused with resume_at=null still allows develop(cursor) (#3091)."""

    def test_develop_dispatches_while_design_engine_paused(self, tmp_path, monkeypatch):
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=3081, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                3081: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            },
            open_issue_rows=[
                {
                    "number": 3081,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            ],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "QUOTA_STATE_PATH", _QUOTA_CLAUDE_PAUSED)
        monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", _ENGINE_DESIGN_CLAUDE)
        now = datetime(2026, 9, 10, 18, 19, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:develop-ready"
        assert result.issue == 3081

    def test_draft_blocked_while_design_engine_paused(self, tmp_path, monkeypatch):
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=3082, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                3082: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "QUOTA_STATE_PATH", _QUOTA_CLAUDE_PAUSED)
        monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", _ENGINE_DESIGN_CLAUDE)
        now = datetime(2026, 9, 10, 18, 19, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "paused" in result.reason
        assert "role=design" in result.reason
        assert "claude" in result.reason

    def test_status_shows_role_in_paused_wait_reason(self, tmp_path, monkeypatch, capsys):
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=3086, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        monkeypatch.setattr(qmod, "QUOTA_STATE_PATH", _QUOTA_CLAUDE_PAUSED)
        monkeypatch.setattr(qmod, "ENGINE_STATE_PATH", _ENGINE_DESIGN_CLAUDE)
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
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
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


class TestTickIssueCache:
    def test_issue_get_memoized_with_union_fields(self):
        from issuesmith import queue as qmod

        client = _CountingClient(issues={1: {"state": "OPEN", "title": "t", "body": "b",
                                             "labels": []}})
        cache = qmod._TickCachedForge(client)
        a = cache.issue_get(1, fields=["state", "labels"])
        b = cache.issue_get(1, fields=["state", "labels", "title", "body", "number"])
        assert a["state"] == b["state"] == "OPEN"
        assert b["body"] == "b"
        assert client.issue_get_count(1) == 1

    def test_write_invalidates_issue(self):
        from issuesmith import queue as qmod

        client = _CountingClient(issues={1: {"state": "OPEN", "labels": []}})
        cache = qmod._TickCachedForge(client)
        cache.issue_get(1, fields=["state"])
        cache.issue_update(1, labels_add=["x"], labels_remove=[])
        cache.issue_get(1, fields=["state"])
        assert client.issue_get_count(1) == 2

    def test_remove_label_invalidates_issue(self):
        from issuesmith import queue as qmod

        client = _CountingClient(issues={1: {"state": "OPEN", "labels": []}})
        client.remove_label = lambda number, label: client.calls.append(("remove_label", number))
        cache = qmod._TickCachedForge(client)
        cache.issue_get(1, fields=["state"])
        cache.remove_label(1, "x")
        cache.issue_get(1, fields=["state"])
        assert client.issue_get_count(1) == 2

    def test_errors_are_memoized(self):
        from issuesmith import queue as qmod

        client = _CountingClient(issues={})
        cache = qmod._TickCachedForge(client)
        for _ in range(2):
            with pytest.raises(KeyError):
                cache.issue_get(404, fields=["state"])
        assert client.issue_get_count(404) == 1

    def test_labeled_open_list_filtered_locally(self):
        from issuesmith import queue as qmod

        client = _CountingClient(issues={
            1: {"state": "OPEN", "labels": [{"name": "scope:milestone"}]},
            2: {"state": "OPEN", "labels": []},
        })
        cache = qmod._TickCachedForge(client)
        ms = cache.api_request("issues?state=open&labels=scope:milestone&per_page=100",
                               paginate=True)
        all_rows = cache.api_request("issues?state=open&per_page=100", paginate=True)
        assert [r["number"] for r in ms] == [1]
        assert sorted(r["number"] for r in all_rows) == [1, 2]
        assert len([c for c in client.calls if c[0] == "api_request"]) == 1
