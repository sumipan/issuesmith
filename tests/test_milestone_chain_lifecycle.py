"""Milestone chain: fixes, closed-parent prune, pre-dispatch/resume and consolidation."""

from __future__ import annotations

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


_NOW = "2026-01-01T12:00:00+00:00"


class TestIssue3130Fixes:
    """AC-8/9/10/11/13/14 for milestone chain (#3130)."""


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


    def test_prune_empty_returns_empty_without_save(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(101, {"stage": "active"})
        before = store.state_path.read_bytes()
        assert store.prune_milestone_chains() == []
        assert store.state_path.read_bytes() == before


_MERGE_DONE = DONE_LABEL["merge"]
_REJECTED = "issuesmith:rejected"


class TestPreDispatchAndResume:


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


def test_classify_child_develop_done_is_merged_single_develop(tmp_path, monkeypatch) -> None:
    """#7811 AC7."""
    import yaml

    from issuesmith.config import get_config, reset_config_cache
    from issuesmith.milestone import _classify_child_terminal

    phases = [
        {
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "writes_files": False,
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "implementation",
            "entry_step": "prepare",
            "handler": "develop",
            "advance_when": ["deps_terminal"],
        },
    ]
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"repo": "sumipan/issuesmith", "phases": phases, "terminal_labels": []}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    _, _, develop_done = get_config().phase_labels("develop")
    child = _child_issue(101, state="CLOSED", labels=[develop_done])
    client = FakeClient(issues={101: child})
    assert _classify_child_terminal(client, 101) == "merged"


class TestConsolidation:


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
