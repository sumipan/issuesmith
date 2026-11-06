"""tests/tools/issuesmith/test_queue.py — QueueStore comprehensive tests."""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import (
    QueueStore,
    QueueValidationError,
    validate_request_fields,
)
from tests.conftest import _write_nexus_config

_NOW = datetime.now(timezone.utc).isoformat()


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


class TestSnapshot:
    def test_empty_snapshot(self, tmp_path):
        store = _store(tmp_path)
        snap = store.snapshot()
        assert snap.active_order == []
        assert snap.halt is False

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

    def test_closed_force_with_done_still_closed(self):
        """CLOSED + force with the requested phase already done → closed (#2825 issue 2)."""
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

    def test_done_label_allows_redispatch_with_force(self):
        """Issues rolled back to develop-done after CP2 FAIL can re-enqueue with --force (2026-09-04).

        F3 FAIL handler documents `enqueue --phase develop ... --force` as recovery,
        but force was not passed into deterministic_decision so requests were always
        rejected as already_processed. Regression reproduction.
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
                "labels": [
                    {"name": "issuesmith:draft-done"},
                    {"name": "issuesmith:develop-done"},
                ],
            },
            force=True,
        )
        assert d.kind == "keep"

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

    def test_ready_label_blocks_even_with_force(self):
        """force=True must not bypass -ready (queued, not started)."""
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
        assert d.kind == "keep"

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
