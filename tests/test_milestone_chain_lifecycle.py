"""Milestone chain: fixes, closed-parent prune, pre-dispatch/resume and consolidation."""

from __future__ import annotations

from datetime import datetime, timezone

from issuesmith.milestone import (
    advance_milestone_chains,
    milestone_status,
    validate_children,
)
from issuesmith.queue_triage import DONE_LABEL
from tests import test_milestone_chain as _base
from tests.test_milestone_chain import (
    _CHILD_BODY,
    _PARENT_BODY,
    CONTENT,
    DEPENDENCY,
    NONE,
    TARGET_REPOSITORY,
    TITLE,
    FakeClient,
    _chain_config,
    _child_issue,
    _parent_issue,
    _store,
)

_reset_cfg = _base._reset_cfg


_NOW = datetime.now(timezone.utc).isoformat()


class TestIssue3130Fixes:
    """AC-8/9/10/11/13/14 for milestone chain (#3130)."""

    def test_v1_title_normalized_with_backticks_and_spaces(self):
        from issuesmith.milestone import check_v3_cjk_placeholders, normalize_plan_title

        assert normalize_plan_title("  `foo   bar`  ") == "foo bar"
        parent_body = (
            _PARENT_BODY
            # ASCII fixture data.
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | `child   work` | `sumipan/nexus` | x | {NONE} |\n"
        )
        parent = {
            "number": 100,
            "body": parent_body,
            "milestone": {"number": 1},
            "labels": [{"name": "scope:milestone"}],
        }
        child = {
            "number": 101,
            "title": "child work",
            "body": _CHILD_BODY,
            "milestone": {"number": 1},
            "labels": [{"name": "issuesmith:draft-done"}],
        }
        result = validate_children(parent, [child], client=FakeClient(issues={101: child}))
        assert result.passed is True
        # ASCII fixture data.
        # prose mention of c30D7_c30EC_c30FC_c30B9_c30DB_c30EB_c30C0 must not trip V3
        assert check_v3_cjk_placeholders(
            body="c672C Issue c306F_c30D7_c30EC_c30FC_c30B9_c30DB_c30EB_c30C0_c691C_c51FA_c3092_c5B9F_c88C5_c3059_c308B\n"
        ) == []

    def test_v1_plan_row_not_found_no_fallback(self):
        parent_body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | expected title | `sumipan/nexus` | x | {NONE} |\n"
        )
        parent = {
            "number": 100,
            "body": parent_body,
            "milestone": {"number": 1},
            "labels": [{"name": "scope:milestone"}],
        }
        child = {
            "number": 101,
            "title": "totally different",
            "body": _CHILD_BODY,
            "milestone": {"number": 1},
            "labels": [{"name": "issuesmith:draft-done"}],
        }
        result = validate_children(parent, [child], client=FakeClient(issues={101: child}))
        assert result.passed is False
        assert any(
            f.startswith("V1 plan row not found for title")
            for f in result.results[0].failures
        )

    def test_v3_standalone_tbd_still_detected(self):
        from issuesmith.milestone import check_v3_cjk_placeholders

        assert check_v3_cjk_placeholders(body="## Design\n\nTBD\n") == [
            "V3 CJK placeholder detected"
        ]
        assert check_v3_cjk_placeholders(
            body="```yaml\ntarget_repo: TBD\n```\n"
        ) == ["V3 CJK placeholder detected"]

    def test_has_request_ignores_rejected_completed(self, tmp_path):
        from issuesmith.milestone import _has_request

        store = _store(tmp_path)
        rid = store.enqueue(
            issue=3166,
            phase="develop",
            source="milestone-chain",
            actor_kind="automation",
            priority="normal",
            requested_by=["milestone-chain"],
            requested_at=_NOW,
            request_id="e82db96f-0000-4000-8000-000000003166",
        ).request_id
        store.complete(rid, "rejected")
        snap = store.snapshot()
        assert _has_request(store, snap, 3166, "develop") is False

    def test_rejected_fixture_reenqueues_develop(self, tmp_path):
        """AC-14: rejected completed develop requests must not block re-enqueue."""
        # Real request_meta prefixes from #3130 investigation.
        fixtures = [
            ("e82db96f-0000-4000-8000-000000003166", 3166, "rejected"),
            ("1466283b-0000-4000-8000-000000003168", 3168, "rejected"),
            ("e7d79eb3-0000-4000-8000-000000003166", 3166, "dequeued"),
            ("e242a525-0000-4000-8000-000000003168", 3168, "skipped"),
            ("ba981aea-0000-4000-8000-000000003100", 3100, "rejected"),
        ]
        store = _store(tmp_path)
        for rid, issue, outcome in fixtures:
            store.enqueue(
                issue=issue,
                phase="develop",
                source="milestone-chain",
                actor_kind="automation",
                priority="normal",
                requested_by=["milestone-chain"],
                requested_at=_NOW,
                request_id=rid,
            )
            store.complete(rid, outcome)

        child_3166 = _child_issue(
            3166, state="OPEN", labels=["issuesmith:draft-done"], title="child 3166"
        )
        child_3168 = _child_issue(
            3168, state="OPEN", labels=["issuesmith:draft-done"], title="child 3168"
        )
        parent = _parent_issue(
            labels=[
                {"name": "scope:milestone"},
                {"name": DONE_LABEL["draft"]},
                {"name": DONE_LABEL["sub"]},
            ]
        )
        client = FakeClient(
            issues={100: parent, 3166: child_3166, 3168: child_3168},
            sub_issues={100: [child_3166, child_3168]},
        )
        store.update_milestone_chain(100, {"stage": "idle"})
        advance_milestone_chains(store, client, _chain_config())
        snap = store.snapshot()
        develop_issues = {
            snap.requests[rid].issue
            for rid in snap.active_order
            if rid in snap.requests and snap.requests[rid].phase == "develop"
        }
        assert 3166 in develop_issues
        assert 3168 in develop_issues

    def test_milestone_status_chain_waiting_line(self, tmp_path, capsys):
        parent = _parent_issue(
            labels=[
                {"name": "scope:milestone"},
                {"name": DONE_LABEL["draft"]},
            ]
        )
        client = FakeClient(
            issues={100: parent},
            comments={},
        )
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "sub_enqueued"})
        rid = store.enqueue(
            issue=100,
            phase="sub",
            source="milestone-chain",
            actor_kind="automation",
            priority="normal",
            requested_by=["milestone-chain"],
            requested_at=_NOW,
            request_id="acb4496f-0000-4000-8000-000000000100",
        ).request_id
        code = milestone_status(100, client=client, store=store)
        assert code == 0
        out = capsys.readouterr().out
        assert f"chain: sub_enqueued (sub request {rid[:8]} active, waiting for sub-done)" in out


class TestClosedParentPrune:
    """#4216: skip/prune completed milestone chains before forge calls."""

    def test_closed_parent_chain_makes_zero_forge_calls(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(
            100,
            {
                "stage": "children_validated",
                "notified_all_done": True,
                "closed_parent": True,
            },
        )
        # Even if another candidate source retains the parent, prune must prevent
        # all forge calls during this tick.
        store.add_in_flight(100, "claude")
        child = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(state="CLOSED"), 101: child},
            children={1: [child]},
            sub_issues={100: [child]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.issue_get_calls.count(100) == 0
        assert client.list_sub_issues_calls.count(100) == 0
        assert client.get_issue_comments_calls.count(100) == 0

    def test_notified_all_done_without_closed_parent_still_evaluated(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(
            100,
            {"stage": "children_validated", "notified_all_done": True},
        )
        child = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child},
            children={1: [child]},
            sub_issues={100: [child]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.issue_get_calls.count(100) >= 1
        assert 100 in client.closed
        chain = store.get_milestone_chain(100)
        assert chain.get("closed_parent") is True

    def test_prune_dry_run_returns_sorted_and_preserves_bytes(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(200, {"stage": "children_validated", "closed_parent": True})
        store.update_milestone_chain(100, {"stage": "children_validated", "closed_parent": True})
        store.update_milestone_chain(150, {"stage": "active"})
        before = store.state_path.read_bytes()
        pruned = store.prune_milestone_chains(dry_run=True)
        assert pruned == [100, 200]
        assert store.state_path.read_bytes() == before

    def test_prune_removes_only_closed_parent_chains(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated", "closed_parent": True})
        store.update_milestone_chain(101, {"stage": "active"})
        store.update_milestone_chain(102, {"stage": "halted", "halted_reason": "x"})
        store.update_milestone_chain(
            103, {"stage": "children_validated", "notified_all_done": True}
        )
        store.enqueue(
            issue=50,
            phase="develop",
            source="test",
            actor_kind="human",
            priority="normal",
            requested_by=["t"],
            requested_at=_NOW,
        )
        before_snap = store.snapshot()
        pruned = store.prune_milestone_chains()
        assert pruned == [100]
        snap = store.snapshot()
        assert "100" not in snap.milestone_chains
        assert snap.milestone_chains["101"]["stage"] == "active"
        assert snap.milestone_chains["102"]["stage"] == "halted"
        assert snap.milestone_chains["103"].get("notified_all_done") is True
        assert snap.active_order == before_snap.active_order
        assert snap.halt == before_snap.halt
        assert snap.revision == before_snap.revision

    def test_prune_empty_returns_empty_without_save(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(101, {"stage": "active"})
        before = store.state_path.read_bytes()
        assert store.prune_milestone_chains() == []
        assert store.state_path.read_bytes() == before

    def test_close_tick_keeps_chain_next_tick_prunes(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child},
            children={1: [child]},
            sub_issues={100: [child]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert store.get_milestone_chain(100).get("closed_parent") is True
        assert "100" in store.snapshot().milestone_chains

        client2 = FakeClient(
            issues={100: dict(client.issues[100]), 101: child},
            children={1: [child]},
            sub_issues={100: [child]},
        )
        advance_milestone_chains(store, client2, _chain_config())
        assert "100" not in store.snapshot().milestone_chains
        assert client2.issue_get_calls.count(100) == 0

    def test_tick_stderr_reports_candidates_skipped_pruned(self, tmp_path, capsys):
        store = _store(tmp_path)
        store.update_milestone_chain(
            100,
            {
                "stage": "children_validated",
                "notified_all_done": True,
                "closed_parent": True,
            },
        )
        store.update_milestone_chain(200, {"stage": "children_validated", "closed_parent": True})
        # Keep #300 open with sub-done so it stays a candidate after prune.
        store.update_milestone_chain(300, {"stage": "children_validated"})
        open_parent = _parent_issue(number=300)
        open_parent_child = _child_issue(
            301, state="OPEN", labels=["issuesmith:draft-done"]
        )
        client = FakeClient(
            issues={
                300: open_parent,
                301: open_parent_child,
            },
            children={1: [open_parent_child]},
            sub_issues={300: [open_parent_child]},
        )
        advance_milestone_chains(store, client, _chain_config())
        err = capsys.readouterr().err
        lines = [
            ln
            for ln in err.splitlines()
            if ln.startswith("[tick] milestone chains candidates=")
        ]
        assert len(lines) == 1
        assert "candidates=1" in lines[0]
        assert "skipped_closed=0" in lines[0]
        assert "pruned=2" in lines[0]


_MERGE_DONE = DONE_LABEL["merge"]
_DRAFT_DONE = DONE_LABEL["draft"]
_REJECTED = "issuesmith:rejected"


def _marker_comment(sibling: int) -> dict:
    return {"body": f"Consolidated into #{sibling}.\n\n<!-- issuesmith:consolidated-into: #{sibling} -->"}


class TestPreDispatchAndResume:
    def test_resume_closed_merged_children_skips_validation_and_closes_parent(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "halted", "halted_reason": "validation failed"})
        c101 = _child_issue(101, state="CLOSED", labels=[_MERGE_DONE], title="renamed one")
        c102 = _child_issue(102, state="CLOSED", labels=[_MERGE_DONE], title="renamed two")
        client = FakeClient(
            issues={100: _parent_issue(), 101: c101, 102: c102},
            children={1: [c101, c102]},
        )
        assert store.resume_milestone_chain(100) is True
        advance_milestone_chains(store, client, _chain_config())
        bodies = [body for _, body in client.posted_comments]
        assert not any("validation-failed" in body for body in bodies)
        assert client.closed == [100]
        assert store.get_milestone_chain(100).get("closed_parent") is True

    def test_c2_validates_and_enqueues_only_pre_dispatch_children(self, tmp_path, monkeypatch):
        import issuesmith.milestone as mmod

        store = _store(tmp_path)
        running = _child_issue(101, state="OPEN", labels=["issuesmith:develop-running"])
        fresh = _child_issue(102, state="OPEN", labels=[_DRAFT_DONE])
        client = FakeClient(
            issues={100: _parent_issue(), 101: running, 102: fresh},
            children={1: [running, fresh]},
        )
        seen: list[list[int]] = []
        real = mmod.validate_children

        def _spy(parent, children, *, client):
            seen.append([c["number"] for c in children])
            return real(parent, children, client=client)

        monkeypatch.setattr(mmod, "validate_children", _spy)
        advance_milestone_chains(store, client, _chain_config())
        assert seen == [[102]]
        assert store.get_milestone_chain(100).get("stage") == "children_validated"
        snap = store.snapshot()
        develop = [
            snap.requests[rid].issue
            for rid in snap.active_order
            if snap.requests[rid].phase == "develop"
        ]
        assert develop == [102]

    def test_c2_pre_dispatch_child_missing_draft_done_still_halts(self, tmp_path):
        store = _store(tmp_path)
        merged = _child_issue(101, state="CLOSED", labels=[_MERGE_DONE])
        fresh = _child_issue(102, state="OPEN", labels=[])
        client = FakeClient(
            issues={100: _parent_issue(), 101: merged, 102: fresh},
            children={1: [merged, fresh]},
        )
        advance_milestone_chains(store, client, _chain_config())
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert chain.get("halted_reason") == "validation failed"
        assert "#102" in client.posted_comments[0][1]
        assert "#101" not in client.posted_comments[0][1]

    def test_c4_halt_saves_stage_and_resume_restores_it(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        c101 = _child_issue(101, state="CLOSED", labels=[_REJECTED])
        c102 = _child_issue(102, state="CLOSED", labels=[_MERGE_DONE])
        client = FakeClient(
            issues={100: _parent_issue(), 101: c101, 102: c102},
            children={1: [c101, c102]},
        )
        advance_milestone_chains(store, client, _chain_config())
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert chain.get("stage_before_halt") == "children_validated"
        assert store.resume_milestone_chain(100) is True
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "children_validated"
        assert "halted_reason" not in chain
        assert "stage_before_halt" not in chain


class TestConsolidation:
    def test_c4_consolidated_child_counts_as_merged(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        c101 = _child_issue(101, state="CLOSED", labels=[_REJECTED])
        c102 = _child_issue(102, state="CLOSED", labels=[_MERGE_DONE])
        client = FakeClient(
            issues={100: _parent_issue(), 101: c101, 102: c102},
            children={1: [c101, c102]},
            comments={101: [_marker_comment(102)]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == [100]
        assert store.get_milestone_chain(100).get("closed_parent") is True

    def test_c4_consolidated_into_open_sibling_stays_without_merge(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        c101 = _child_issue(101, state="CLOSED", labels=[_REJECTED])
        c102 = _child_issue(102, state="CLOSED", labels=[_REJECTED])
        c103 = _child_issue(103, state="CLOSED", labels=[_MERGE_DONE])
        # 101 -> 102 (closed without merge), 102 -> 999 (outside the chain).
        client = FakeClient(
            issues={100: _parent_issue(), 101: c101, 102: c102, 103: c103},
            children={1: [c101, c102, c103]},
            comments={101: [_marker_comment(102)], 102: [_marker_comment(999)]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == []
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert chain.get("halted_reason") == "child closed without merge-done: #101"
        body = client.posted_comments[0][1]
        assert "#101, #102" in body
        assert "milestone consolidate" in body
        assert "milestone resume 100" in body

    def test_c4_consolidated_into_open_sibling_is_not_terminal(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        c101 = _child_issue(101, state="CLOSED", labels=[_REJECTED])
        c102 = _child_issue(102, state="OPEN", labels=["issuesmith:develop-running"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: c101, 102: c102},
            children={1: [c101, c102]},
            comments={101: [_marker_comment(102)]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == []
        assert client.posted_comments == []
        # Sibling merges later without merge-done on 101 → still resolves via marker.
        client.issues[102] = _child_issue(102, state="CLOSED", labels=[_MERGE_DONE])
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == [100]

    def test_consolidate_handles_merged_and_rejected_children(self, tmp_path, monkeypatch):
        from issuesmith.milestone import milestone_consolidate

        parent_body = (
            _PARENT_BODY
            + "\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|---|---|---|---|\n"
            f"| 1 | sub one | `sumipan/nexus` | x | {NONE} |\n"
            f"| 2 | sub two | `sumipan/nexus` | x | {NONE} |\n"
            f"| 3 | sub three | `sumipan/nexus` | x | {NONE} |\n"
            f"| 4 | sub four | `sumipan/nexus` | x | {NONE} |\n"
            f"| 5 | sub five | `sumipan/nexus` | x | {NONE} |\n"
        )
        kids = [
            _child_issue(201, state="CLOSED", labels=[_MERGE_DONE], title="sub one merged with two"),
            _child_issue(202, state="CLOSED", labels=[_REJECTED], title="sub two"),
            _child_issue(203, state="CLOSED", labels=[_MERGE_DONE], title="sub three"),
            _child_issue(204, state="CLOSED", labels=[_MERGE_DONE], title="sub four"),
            _child_issue(205, state="CLOSED", labels=[_MERGE_DONE], title="sub five"),
        ]
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "halted", "halted_reason": "validation failed"})
        client = FakeClient(
            issues={100: _parent_issue(body=parent_body), **{k["number"]: k for k in kids}},
            children={1: kids},
        )
        # (a) resume → C4 needs-review halt, no validation-failed comment.
        assert store.resume_milestone_chain(100) is True
        advance_milestone_chains(store, client, _chain_config())
        bodies = [body for _, body in client.posted_comments]
        assert not any("validation-failed" in body for body in bodies)
        assert any("closed-without-merge" in body for body in bodies)
        assert client.closed == []
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert chain.get("stage_before_halt") == "children_validated"

        # (b) consolidate → resume → tick closes the parent.
        monkeypatch.setattr("issuesmith.milestone.get_forge", lambda: client)
        assert milestone_consolidate(202, 201, client=client) == 0
        assert store.resume_milestone_chain(100) is True
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == [100]
        assert store.get_milestone_chain(100).get("closed_parent") is True
