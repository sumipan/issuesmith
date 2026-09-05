"""tests/tools/issuesmith/test_queue.py — QueueStore comprehensive tests."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from issuesmith.queue_store import (
    QueueStore,
    QueueValidationError,
    validate_request_fields,
)

_NOW = datetime.now(timezone.utc).isoformat()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


class TestEnqueue:
    def test_enqueue_creates_request(self, tmp_path):
        store = _store(tmp_path)
        result = store.enqueue(
            issue=100, phase="draft", source="test", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        assert result.created is True
        assert result.request_id

        snap = store.snapshot()
        assert len(snap.active_order) == 1
        assert snap.requests[result.request_id].issue == 100

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

    def test_enqueue_different_phases_creates_separate(self, tmp_path):
        store = _store(tmp_path)
        r1 = store.enqueue(
            issue=100, phase="draft", source="test", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        r2 = store.enqueue(
            issue=100, phase="develop", source="test", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        assert r1.created is True
        assert r2.created is True
        assert r1.request_id != r2.request_id

        snap = store.snapshot()
        assert len(snap.active_order) == 2

    def test_enqueue_force_flag(self, tmp_path):
        store = _store(tmp_path)
        r = store.enqueue(
            issue=200, phase="merge", source="recovery", actor_kind="human",
            priority="high", requested_by=["admin"], requested_at=_NOW,
            force=True,
        )
        snap = store.snapshot()
        assert store.is_force(snap, r.request_id) is True


class TestComplete:
    def test_complete_removes_from_active(self, tmp_path):
        store = _store(tmp_path)
        r = store.enqueue(
            issue=100, phase="draft", source="test", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        store.complete(r.request_id, "dispatched")
        snap = store.snapshot()
        assert r.request_id not in snap.active_order
        assert r.request_id in snap.completed_request_ids


class TestHalt:
    def test_set_and_clear_halt(self, tmp_path):
        store = _store(tmp_path)
        store.set_halt(True, "manual stop")
        snap = store.snapshot()
        assert snap.halt is True
        assert snap.halt_reason == "manual stop"

        store.clear_halt()
        snap = store.snapshot()
        assert snap.halt is False
        assert snap.halt_reason is None


class TestLastIssue:
    def test_set_last_issue(self, tmp_path):
        store = _store(tmp_path)
        store.set_last_issue(42)
        snap = store.snapshot()
        assert snap.last_issue == 42


class TestSnapshot:
    def test_empty_snapshot(self, tmp_path):
        store = _store(tmp_path)
        snap = store.snapshot()
        assert snap.active_order == []
        assert snap.halt is False
        assert snap.last_issue is None

    def test_effective_request(self, tmp_path):
        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="human",
            priority="low", requested_by=["x"], requested_at=_NOW,
        )
        snap = store.snapshot()
        eff = store.effective_request(snap, r.request_id)
        assert eff.issue == 1
        assert eff.priority == "low"


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


class TestConcurrency:
    def test_concurrent_enqueue(self, tmp_path):
        store = _store(tmp_path)
        results = []

        def _enqueue(issue: int) -> None:
            r = store.enqueue(
                issue=issue, phase="draft", source="test", actor_kind="human",
                priority="normal", requested_by=["t"], requested_at=_NOW,
            )
            results.append(r)

        threads = [threading.Thread(target=_enqueue, args=(i,)) for i in range(1, 6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        snap = store.snapshot()
        assert len(snap.active_order) == 5
        assert all(r.created for r in results)


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


class TestReplaceOrder:
    def test_replace_order_reorders(self, tmp_path):
        store = _store(tmp_path)
        r1 = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="human",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        r2 = store.enqueue(
            issue=2, phase="draft", source="s", actor_kind="human",
            priority="high", requested_by=["b"], requested_at=_NOW,
        )
        snap = store.snapshot()
        ok = store.replace_order(snap.revision, [r2.request_id, r1.request_id])
        assert ok is True
        snap2 = store.snapshot()
        assert snap2.active_order == [r2.request_id, r1.request_id]

    def test_replace_order_stale_revision(self, tmp_path):
        store = _store(tmp_path)
        store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="human",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()
        store.enqueue(
            issue=2, phase="draft", source="s", actor_kind="human",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        ok = store.replace_order(snap.revision, snap.active_order)
        assert ok is False


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
        """CLOSED + force かつ要求フェーズの done 未付与なら keep（#2825 問題2）."""
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

    def test_closed_force_with_done_still_closed(self):
        """CLOSED + force でも要求フェーズの done 済みなら closed（#2825 問題2）."""
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
                "labels": [{"name": "issuesmith:merge-done"}],
            },
            force=True,
        )
        assert d.kind == "closed"

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
        """develop-done 済み Issue への develop 再投入は force なしでは弾かれる (2026-09-04)."""
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

    def test_done_label_allows_redispatch_with_force(self):
        """CP2 FAIL で develop-done に差し戻された Issue は --force で再投入できる (2026-09-04)。

        F3 の FAIL ハンドラは `enqueue --phase develop ... --force` を復旧手順として
        案内するが、force が deterministic_decision に伝わっておらず常に
        already_processed で弾かれていた回帰の再現テスト。
        """
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
            force=True,
        )
        assert d.kind == "keep"

    def test_running_label_blocks_even_with_force(self):
        """force=True でも -running（現在実行中）は bypass しない — #2813 の二重投入対策と矛盾させない。"""
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

    def test_ready_label_blocks_even_with_force(self):
        """force=True でも -ready（投入済み・未着手）は bypass しない。"""
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "draft", "recovery",
            "human", "high", _NOW, ("sumipan",),
        )
        d = deterministic_decision(
            req,
            {
                "state": "OPEN",
                "title": "x",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:draft-ready"}],
            },
            force=True,
        )
        assert d.kind == "already_processed"

    def test_missing_yaml(self):
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            "11111111-1111-1111-1111-111111111111", 1, "draft", "s",
            "automation", "low", _NOW, ("bot",),
        )
        d = deterministic_decision(req, {"state": "OPEN", "title": "x", "body": "no yaml", "labels": []})
        assert d.kind == "rejected"
        assert d.add_rejected_label is True

    def test_superseded_newer_semver(self):
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
                "title": "nexus: bump mltgnt to v0.22.7",
                "body": _VALID_BODY,
                "labels": [],
            },
            open_issues=[
                {
                    "state": "OPEN",
                    "number": 11,
                    "title": "nexus: bump mltgnt to v0.23.0",
                }
            ],
        )
        assert d.kind == "superseded"
        assert d.close_issue is True

    def test_older_semver_not_superseded(self):
        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import deterministic_decision

        req = QueueRequest(
            request_id="00000000-0000-4000-8000-000000000099",
            issue=99,
            phase="draft",
            source="release-watcher",
            actor_kind="automation",
            priority="low",
            requested_at=_NOW,
            requested_by=("release-watcher",),
        )
        issue = {
            "number": 99,
            "state": "OPEN",
            "title": "nexus: bump mltgnt to v0.22.0",
            "body": _VALID_BODY,
            "labels": [],
        }
        open_issues = [
            issue,
            {
                "number": 80,
                "state": "OPEN",
                "title": "nexus: bump mltgnt to v0.21.0",
                "body": _VALID_BODY,
                "labels": [],
            },
        ]
        d = deterministic_decision(req, issue, open_issues=open_issues)
        assert d.kind == "keep"
        assert d.close_issue is False

    def test_same_semver_not_superseded(self):
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
                "title": "nexus: bump mltgnt to v0.23.0",
                "body": _VALID_BODY,
                "labels": [],
            },
            open_issues=[
                {
                    "state": "OPEN",
                    "number": 11,
                    "title": "nexus: bump mltgnt to v0.23.0",
                }
            ],
        )
        assert d.kind == "keep"


class TestTriage:
    def test_llm_adopt_and_constraints(self, tmp_path):
        from datetime import timedelta

        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        r_low = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        r_high = store.enqueue(
            issue=2, phase="draft", source="s", actor_kind="automation",
            priority="high", requested_by=["a"], requested_at=_NOW,
        )
        r_aged = store.enqueue(
            issue=3, phase="draft", source="s", actor_kind="automation",
            priority="normal", requested_by=["a"], requested_at=old,
        )
        snap = store.snapshot()

        def fake_llm(prompt, **kwargs):
            order = [r_low.request_id, r_aged.request_id, r_high.request_id]
            payload = {
                "order": order,
                "decisions": [
                    {"request_id": rid, "decision": "keep", "reason": "ok", "uncertain_flag": False}
                    for rid in order
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={
                1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"},
                2: {"title": "b", "body": _VALID_BODY, "labels": [], "state": "OPEN"},
                3: {"title": "c", "body": _VALID_BODY, "labels": [], "state": "OPEN"},
            },
            store=store,
            call_llm=fake_llm,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is True
        assert result.order[0] == r_high.request_id
        assert r_aged.request_id in result.order
        # aged normal before low
        assert result.order.index(r_aged.request_id) < result.order.index(r_low.request_id)
        log = (tmp_path / "triage.jsonl").read_text(encoding="utf-8")
        assert "prompt_version" in log
        assert "trace_id" in log

    def test_human_reject_converted_to_keep(self, tmp_path):
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        snap = store.snapshot()

        def fake_llm(prompt, **kwargs):
            payload = {
                "order": [r.request_id],
                "decisions": [
                    {
                        "request_id": r.request_id,
                        "decision": "reject",
                        "reason": "noise",
                        "uncertain_flag": False,
                    }
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=fake_llm,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.decisions[0].decision == "keep"

    def test_llm_failure_falls_back(self, tmp_path):
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()

        def boom(prompt, **kwargs):
            raise TimeoutError("timeout")

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=boom,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is False
        assert result.order == [r.request_id]
        assert "timeout" in (result.fallback_reason or "")
        assert (tmp_path / "triage.jsonl").exists()

    def test_default_engine_model_is_allowlisted(self):
        """triage デフォルト engine/model が configs allowlist に存在すること (#2803)."""
        from ghdag.llm import EngineModelError, validate_engine_model

        validate_engine_model("claude", "claude-sonnet-4-6")
        with pytest.raises(EngineModelError):
            validate_engine_model("cursor", "gemini-3-flash")

    def test_engine_model_mismatch_is_config_error(self, tmp_path):
        """engine/model 不整合は llm failed ではなく config error として区別される (#2803)."""
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()
        called = {"n": 0}

        def fake_llm(prompt, **kwargs):
            called["n"] += 1
            payload = {
                "order": [r.request_id],
                "decisions": [
                    {
                        "request_id": r.request_id,
                        "decision": "keep",
                        "reason": "ok",
                        "uncertain_flag": False,
                    }
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=fake_llm,
            engine="cursor",
            model="gemini-3-flash",
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is False
        assert called["n"] == 0
        assert (result.fallback_reason or "").startswith("config error:")
        assert "llm failed:" not in (result.fallback_reason or "")
        log = json.loads((tmp_path / "triage.jsonl").read_text(encoding="utf-8").strip())
        assert log["config"]["engine"] == "cursor"
        assert log["config"]["model"] == "gemini-3-flash"
        assert (log.get("fallback_reason") or "").startswith("config error:")

    def test_default_triage_logs_claude_engine_model(self, tmp_path):
        """デフォルト呼び出しの triage ログに claude / claude-sonnet-4-6 が記録される (#2803)."""
        from issuesmith.queue_triage import triage

        store = _store(tmp_path)
        r = store.enqueue(
            issue=1, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["a"], requested_at=_NOW,
        )
        snap = store.snapshot()

        def fake_llm(prompt, **kwargs):
            assert kwargs.get("engine") == "claude"
            assert kwargs.get("model") == "claude-sonnet-4-6"
            payload = {
                "order": [r.request_id],
                "decisions": [
                    {
                        "request_id": r.request_id,
                        "decision": "keep",
                        "reason": "ok",
                        "uncertain_flag": False,
                    }
                ],
            }

            class R:
                text = json.dumps(payload)
                usage = None

            return R()

        result = triage(
            snap,
            issues={1: {"title": "a", "body": _VALID_BODY, "labels": [], "state": "OPEN"}},
            store=store,
            call_llm=fake_llm,
            triage_log_path=tmp_path / "triage.jsonl",
        )
        assert result.adopted is True
        log = json.loads((tmp_path / "triage.jsonl").read_text(encoding="utf-8").strip())
        assert log["config"]["engine"] == "claude"
        assert log["config"]["model"] == "claude-sonnet-4-6"

    def test_no_hardcoded_cursor_gemini_in_triage_source(self):
        """LLM 呼び出し・ログ箇所にハードコードされた engine/model が残っていないこと (#2803)."""
        import issuesmith.queue_triage as triage_mod

        src = Path(triage_mod.__file__).read_text(encoding="utf-8")
        assert 'engine="cursor"' not in src
        assert 'engine="cursor", model="gemini-3-flash"' not in src
        assert '"engine": "cursor"' not in src
        assert '"model": "gemini-3-flash"' not in src
        assert 'engine="gemini"' not in src


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

    def test_reset_clears_halt_and_last_issue(self, tmp_path, capsys):
        """reset は halt と last_issue を両方クリアする (#2808)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_last_issue(2297)
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
        assert snap.halt is False
        assert snap.last_issue is None
        out = capsys.readouterr().out
        assert "halt=false" in out
        assert "last_issue=None" in out

    def test_reset_keep_last_issue(self, tmp_path, capsys):
        """--keep-last-issue では halt のみクリアし last_issue を残す (#2808)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_last_issue(2297)
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
                "--keep-last-issue",
            ]
        )
        assert code == 0
        snap = store.snapshot()
        assert snap.halt is False
        assert snap.last_issue == 2297
        out = capsys.readouterr().out
        assert "halt=false" in out
        assert "last_issue=2297 (kept)" in out

    def test_status_shows_halt_reason(self, tmp_path, capsys):
        """halt=true のとき status は halt_reason を表示する (#2808)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_last_issue(2297)
        store.set_halt(True, "last_issue #2297 is still OPEN while pipeline idle")
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
        assert "halt=True" in out or "halt=true" in out
        assert "last_issue=2297" in out
        assert "halt_reason:" in out
        assert "last_issue #2297 is still OPEN" in out
        assert "issue=#2808" in out

    def test_status_warns_when_last_issue_closed_without_terminal(self, tmp_path, capsys, monkeypatch):
        """last_issue が CLOSED かつ終端ラベル無しなら status が警告する (#2825)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_last_issue(2820)

        class FakeClient:
            def __init__(self, *a, **k):
                pass

            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "CLOSED",
                    "labels": [{"name": "scope:milestone"}],
                }

        monkeypatch.setattr(qmod, "GitHubClient", FakeClient)
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
        assert "last_issue=2820" in out
        assert "warning:" in out
        assert "#2820" in out
        assert "CLOSED without terminal" in out or "terminal label" in out


class TestDispatch:
    def test_dispatch_draft_when_gates_pass(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )

        class Client:
            def __init__(self):
                self.updates = []
                self.comments = []

            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }

            def issue_update(self, number, labels_add=None, labels_remove=None):
                self.updates.append((number, labels_add))

            def issue_comment(self, number, body):
                self.comments.append((number, body))

            def get_issue_comments(self, number):
                return []

            def list_issues(self, label, state="open"):
                return []

            def list_open_issues_for_queue(self):
                return []

        client = Client()
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:draft-ready"
        assert client.updates == [(50, ["issuesmith:draft-ready"])]
        snap = store.snapshot()
        assert snap.active_order == []
        assert snap.last_issue == 50

    def test_dispatch_blocked_when_running_elsewhere(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
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
                if label.endswith("-running"):
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
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
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

    def test_comment_marker_idempotent(self, tmp_path):
        from issuesmith import queue as qmod
        from issuesmith.queue_triage import comment_marker

        store = _store(tmp_path)
        r = store.enqueue(
            issue=7, phase="draft", source="s", actor_kind="automation",
            priority="low", requested_by=["bot"], requested_at=_NOW,
        )
        marker = comment_marker(r.request_id, "rejected")

        class Client:
            def __init__(self):
                self.comments = []

            def get_issue_comments(self, number):
                return [{"body": f"already\n{marker}"}]

            def issue_comment(self, number, body):
                self.comments.append(body)

            def issue_update(self, *a, **k):
                pass

            def issue_close(self, *a):
                pass

        client = Client()
        qmod._ensure_comment(client, 7, r.request_id, "rejected", "reason")
        assert client.comments == []
        qmod._apply_terminal(store, client, r.request_id, 7, "rejected", "reason", add_rejected_label=True)
        # second apply is no-op for comment
        qmod._apply_terminal(store, client, r.request_id, 7, "rejected", "reason", add_rejected_label=True)
        assert client.comments == []

    def test_dispatch_blocked_outside_source_window(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="release-watcher", actor_kind="automation",
            priority="low", requested_by=["release-watcher"], requested_at=_NOW,
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
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        # Outside 01:00–07:00 JST window
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=Client(),
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "no dispatchable" in result.reason
        assert store.snapshot().active_order  # request retained

    def test_dispatch_blocked_when_engines_paused(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
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
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: ["claude"])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=Client(),
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "paused" in result.reason

    def test_dispatch_develop_requires_draft_done(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )

        class Client:
            def __init__(self):
                self.updates = []

            def issue_get(self, number, fields=None):
                return {
                    "number": number,
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],  # no draft-done
                }

            def issue_update(self, number, labels_add=None, labels_remove=None):
                self.updates.append((number, labels_add))

            def list_issues(self, label, state="open"):
                return []

            def get_issue_comments(self, number):
                return []

            def list_open_issues_for_queue(self):
                return []

        client = Client()
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert client.updates == []
        assert store.snapshot().active_order  # retained


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
            return list(self.open_prs)
        return []


class TestCp2MergePrDetection:
    def test_phase_preconditions_merge_finds_closes_in_body(self):
        """PR body に Closes #N がある通常ケースで merge 事前条件が通る."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            open_prs=[
                {
                    "number": 2789,
                    "title": "実装: Issue #2773",
                    "body": "P1/P2 result より自動生成。\n\nCloses #2773",
                    "state": "OPEN",
                    "headRefName": "feat/issue-2773",
                }
            ]
        )
        issue = {
            "number": 2773,
            "state": "OPEN",
            "title": "t",
            "body": _VALID_BODY,
            "labels": [],
        }
        ok, why = qmod._phase_preconditions("merge", issue, client, 2773)
        assert ok is True, why
        assert client.pr_get_calls == [2789]

    def test_phase_preconditions_merge_finds_refs_in_body(self):
        """publish.py はもう Closes を発行しない（2026-09-06）。Refs #N でも見つかること。"""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            open_prs=[
                {
                    "number": 2789,
                    "title": "実装: Issue #2773",
                    "body": "P1/P2 result より自動生成。\n\nRefs #2773",
                    "state": "OPEN",
                    "headRefName": "feat/issue-2773",
                }
            ]
        )
        issue = {
            "number": 2773,
            "state": "OPEN",
            "title": "t",
            "body": _VALID_BODY,
            "labels": [],
        }
        ok, why = qmod._phase_preconditions("merge", issue, client, 2773)
        assert ok is True, why

    def test_phase_preconditions_merge_search_alone_insufficient(self):
        """pr_list(search='Closes #N') は body を見ないので、pr_get が必須."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            open_prs=[
                {
                    "number": 1,
                    "title": "実装: Issue #10",
                    "body": "Closes #10",
                    "state": "OPEN",
                    "headRefName": "feat/x",
                }
            ]
        )
        # search-only path would miss this; our helper must still find it
        listed = client.pr_list(state="open", search="Closes #10")
        assert listed == []  # production search does not match body
        ok, why = qmod._phase_preconditions("merge", {"state": "OPEN", "labels": []}, client, 10)
        assert ok is True, why

    def test_phase_preconditions_merge_already_merged(self):
        """オープン PR 無し・既マージ PR あり・merge-done 未付与 → already_merged (#2825)."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(
            closed_prs=[
                {
                    "number": 2900,
                    "title": "実装: Issue #2825",
                    "body": "Closes #2825",
                    "state": "CLOSED",
                    "headRefName": "feat/issue-2825",
                    "mergedAt": "2026-09-04T12:00:00Z",
                }
            ]
        )
        ok, why = qmod._phase_preconditions(
            "merge",
            {"state": "OPEN", "labels": [{"name": "issuesmith:develop-done"}]},
            client,
            2825,
        )
        assert ok is True
        assert why == "already_merged"

    def test_phase_preconditions_merge_no_open_or_merged(self):
        """オープンもマージ済みも無し → 拒否メッセージ (#2825)."""
        from issuesmith import queue as qmod

        client = _ProdShapeClient(open_prs=[], closed_prs=[])
        ok, why = qmod._phase_preconditions("merge", {"state": "OPEN", "labels": []}, client, 10)
        assert ok is False
        assert "no open or merged PR" in why

    def test_dispatch_merge_when_open_pr_closes_in_body(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=2773, phase="merge", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                2773: {
                    "state": "OPEN",
                    "title": "queue serialize",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:develop-done"}],
                }
            },
            open_prs=[
                {
                    "number": 2789,
                    "title": "実装: Issue #2773",
                    "body": "P1/P2 result より自動生成。\n\nCloses #2773",
                    "state": "OPEN",
                    "headRefName": "feat/issue-2773",
                }
            ],
            open_issue_rows=[
                {
                    "number": 2773,
                    "title": "queue serialize",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:develop-done"}],
                }
            ],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:merge-ready"
        assert any(u[0] == 2773 and "issuesmith:merge-ready" in (u[1] or []) for u in client.updates)


class TestCp2SupersedeRepoWide:
    def test_supersede_scans_open_issues_outside_queue(self, tmp_path, monkeypatch):
        """キューに無い新しい同種 SemVer Issue でも superseded になる."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=10, phase="draft", source="release-watcher", actor_kind="automation",
            priority="low", requested_by=["release-watcher"], requested_at=_NOW,
        )
        # Newer same-kind issue #11 is NOT in the queue
        client = _ProdShapeClient(
            issues={
                10: {
                    "state": "OPEN",
                    "title": "nexus: bump mltgnt to v0.22.7",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[
                {
                    "number": 10,
                    "title": "nexus: bump mltgnt to v0.22.7",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                },
                {
                    "number": 11,
                    "title": "nexus: bump mltgnt to v0.23.0",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                },
            ],
        )
        assert not hasattr(client, "list_open_issues_for_queue")
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        # Inside release-watcher window
        now = datetime(2026, 9, 3, 3, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert 10 in client.closes
        assert any("issues?" in p for p in client.api_request_calls)
        snap = store.snapshot()
        assert snap.active_order == []
        assert len(snap.completed_request_ids) == 1


class TestCp2LlmRelativeOrder:
    def test_same_priority_preserves_llm_relative_order(self):
        """同一 priority / aging 区分では LLM の相対順序を requested_at で上書きしない."""
        from datetime import timedelta

        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import apply_deterministic_order_constraints

        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        older = (now - timedelta(days=1)).isoformat()
        newer = now.isoformat()
        r_old = QueueRequest(
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            1, "draft", "s", "automation", "normal", older, ("a",),
        )
        r_new = QueueRequest(
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            2, "draft", "s", "automation", "normal", newer, ("b",),
        )
        llm_order = [r_new.request_id, r_old.request_id]
        result = apply_deterministic_order_constraints(
            llm_order, {r_old.request_id: r_old, r_new.request_id: r_new}, now=now
        )
        assert result == llm_order

    def test_high_and_aged_still_override_llm(self):
        from datetime import timedelta

        from issuesmith.queue_store import QueueRequest
        from issuesmith.queue_triage import apply_deterministic_order_constraints

        now = datetime(2026, 9, 3, tzinfo=timezone.utc)
        aged_at = (now - timedelta(days=8)).isoformat()
        r_low = QueueRequest(
            "11111111-1111-4111-8111-111111111111",
            1, "draft", "s", "automation", "low", now.isoformat(), ("a",),
        )
        r_high = QueueRequest(
            "22222222-2222-4222-8222-222222222222",
            2, "draft", "s", "automation", "high", now.isoformat(), ("a",),
        )
        r_aged = QueueRequest(
            "33333333-3333-4333-8333-333333333333",
            3, "draft", "s", "automation", "normal", aged_at, ("a",),
        )
        llm_order = [r_low.request_id, r_aged.request_id, r_high.request_id]
        result = apply_deterministic_order_constraints(
            llm_order,
            {
                r_low.request_id: r_low,
                r_high.request_id: r_high,
                r_aged.request_id: r_aged,
            },
            now=now,
        )
        assert result[0] == r_high.request_id
        assert result.index(r_aged.request_id) < result.index(r_low.request_id)


class TestCp2DevelopDispatch:
    def test_dispatch_develop_when_draft_done(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            },
            open_issue_rows=[
                {
                    "number": 50,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:draft-done"}],
                }
            ],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is True
        assert result.label == "issuesmith:develop-ready"

    def test_dispatch_blocked_when_pipeline_not_idle(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", lambda snap, idle, now: False)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "idle" in result.reason
        assert store.snapshot().active_order

    def test_dispatch_blocked_when_previous_issue_not_done(self, tmp_path, monkeypatch):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.set_last_issue(40)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                40: {
                    "state": "OPEN",
                    "title": "prev",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:develop-running"}],
                },
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                },
            },
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        result = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert result.dispatched is False
        assert "halted" in result.reason or "previous" in result.reason or "merge-done" in result.reason

    def test_dispatch_label_boundary_rerun_is_idempotent(self, tmp_path, monkeypatch):
        """ready 付与直後に停止→再実行してもラベル二重付与せず terminal になる."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from issuesmith import queue as qmod

        store = _store(tmp_path)
        r = store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={
                50: {
                    "state": "OPEN",
                    "title": "t",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            },
            open_issue_rows=[
                {
                    "number": 50,
                    "title": "t",
                    "state": "open",
                    "body": _VALID_BODY,
                    "labels": [],
                }
            ],
        )

        original_update = client.issue_update

        def tracking_update(number, labels_add=None, labels_remove=None):
            original_update(number, labels_add=labels_add, labels_remove=labels_remove)
            for lab in labels_add or []:
                client.issues[50]["labels"].append({"name": lab})

        client.issue_update = tracking_update  # type: ignore[method-assign]

        monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
        monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
        now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
        first = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert first.dispatched is True
        second = qmod.dispatch_one(
            now=now,
            client=client,
            store=store,
            skip_seed=True,
            call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
        )
        assert second.dispatched is False
        ready_adds = [
            u for u in client.updates
            if u[1] and "issuesmith:draft-ready" in u[1]
        ]
        assert len(ready_adds) == 1
        assert r.request_id in store.snapshot().completed_request_ids


# --- #2825: terminal-without-merge gate / superseded label ---


@pytest.mark.parametrize(
    "terminal_label",
    [
        "issuesmith:sub-ready",
        "issuesmith:sub-done",
        "issuesmith:rejected",
        "issuesmith:superseded",
    ],
)
def test_dispatch_allows_when_previous_issue_terminal_without_merge(
    tmp_path, monkeypatch, terminal_label
):
    """last_issue が CLOSED + TERMINAL_WITHOUT_MERGE なら次をディスパッチできる (#2825)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(40)
    store.enqueue(
        issue=50, phase="draft", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )
    client = _ProdShapeClient(
        issues={
            40: {
                "state": "CLOSED",
                "title": "prev",
                "body": _VALID_BODY,
                "labels": [{"name": terminal_label}],
            },
            50: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            },
        },
        open_issue_rows=[
            {
                "number": 50,
                "title": "t",
                "state": "open",
                "body": _VALID_BODY,
                "labels": [],
            }
        ],
    )
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is True
    assert result.issue == 50
    assert store.snapshot().halt is False


def test_dispatch_halts_when_previous_closed_without_terminal(tmp_path, monkeypatch):
    """CLOSED だが終端ラベル無しの last_issue は halt して理由を残す (#2825)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.set_last_issue(2820)
    store.enqueue(
        issue=50, phase="draft", source="skill", actor_kind="human",
        priority="normal", requested_by=["alice"], requested_at=_NOW,
    )
    client = _ProdShapeClient(
        issues={
            2820: {
                "state": "CLOSED",
                "title": "milestone",
                "body": _VALID_BODY,
                "labels": [{"name": "scope:milestone"}],
            },
            50: {
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            },
        },
        open_issue_rows=[],
    )
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))
    result = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )
    assert result.dispatched is False
    assert "previous" in result.reason or "terminal" in result.reason
    snap = store.snapshot()
    assert snap.halt is True
    assert snap.halt_reason is not None
    assert "#2820" in snap.halt_reason
    assert "scope:milestone" in snap.halt_reason


def test_apply_terminal_superseded_adds_label(tmp_path):
    """superseded 終端時に issuesmith:superseded ラベルを付与する (#2825)."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=7, phase="draft", source="s", actor_kind="automation",
        priority="low", requested_by=["bot"], requested_at=_NOW,
    )

    class Client:
        def __init__(self):
            self.updates = []
            self.closes = []

        def get_issue_comments(self, number):
            return []

        def issue_comment(self, number, body):
            pass

        def issue_update(self, number, labels_add=None, labels_remove=None):
            self.updates.append((number, labels_add, labels_remove))

        def issue_close(self, number):
            self.closes.append(number)

    client = Client()
    qmod._apply_terminal(
        store, client, r.request_id, 7, "superseded", "superseded by #8",
        close_issue=True,
    )
    assert any(
        u[1] and "issuesmith:superseded" in u[1]
        for u in client.updates
    )
    assert client.closes == [7]


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
