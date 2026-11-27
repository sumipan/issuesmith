"""Milestone chain: sub-issue linking, enumeration and SUB1 public API."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from issuesmith.milestone import (
    PlanRow,
    SplitPlanError,
    _list_open_milestones,
    advance_milestone_chains,
    allow_paths_for_row,
    build_child_body,
    ensure_sub1_binding,
    link_sub_issue,
    parse_split_plan,
    plan_section,
)
from tests import test_milestone_chain as _base
from tests.test_milestone_chain import (
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


class _ApiError(Exception):
    """Minimal stand-in for ghdag GitHubApiError (status_code attribute)."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class TestLinkSubIssue:
    def test_link_sub_issue_success_sets_sub_issue_link(self):
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {"number": 100, "state": "OPEN", "labels": [], "milestone": {"number": 1}}
        client = FakeClient(issues={100: parent, 101: child})
        assert link_sub_issue(client, 100, 101) is True
        assert client.sub_issue_links == [(100, 5407000506)]


    def test_link_sub_issue_http_422_raised_is_still_idempotent(self):
        """Defense: if client raises 422, treat as idempotent success."""
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {"number": 100, "state": "OPEN", "labels": [], "milestone": {"number": 1}}
        client = FakeClient(
            issues={100: parent, 101: child},
            add_sub_issue_error=_ApiError("duplicate sub-issue", status_code=422),
        )
        assert link_sub_issue(client, 100, 101) is True
        assert client.sub_issue_links == []


    def test_ensure_sub1_binding_no_milestone_link_ok_continues(self):
        """#3059: if sub-issue link succeeds without milestone, SUB1 does not stop."""
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {
            "number": 100,
            "state": "OPEN",
            "labels": [{"name": "scope:milestone"}],
            "milestone": None,
        }
        client = FakeClient(issues={100: parent, 101: child})
        assert ensure_sub1_binding(client, 100, 101) is True
        assert client.sub_issue_links == [(100, 5407000506)]
        assert client.posted_comments == []

    def test_ensure_sub1_binding_no_milestone_link_fail_stops_with_comment(self):
        """No milestone and link failure → post error comment and return False."""
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {
            "number": 100,
            "state": "OPEN",
            "labels": [{"name": "scope:milestone"}],
            "milestone": None,
        }
        client = FakeClient(
            issues={100: parent, 101: child},
            add_sub_issue_error=_ApiError("boom", status_code=500),
        )
        assert ensure_sub1_binding(client, 100, 101) is False
        assert len(client.posted_comments) == 1
        body = client.posted_comments[0][1]
        # ASCII fixture data.
        assert "milestone" in body
        assert "#101" in body
        assert "<!-- issuesmith:sub1:no-milestone-no-sub-link -->" in body


class TestSubIssuesEnumeration:
    """#3127: enumerate children via list_sub_issues, with milestone fallback."""


    def test_no_fallback_without_milestone_when_sub_issues_empty(self, tmp_path):
        store = _store(tmp_path)
        parent = _parent_issue(milestone=None)
        client = FakeClient(
            issues={100: parent},
            sub_issues={100: []},
            children={1: [_child_issue(101, state="OPEN", labels=["issuesmith:draft-done"])]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.list_sub_issues_calls == [100]
        assert not any("milestone=" in path for path in client.api_request_calls)
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert chain.get("halted_reason") == "no children"

    def test_sub_issues_works_without_milestone_object(self, tmp_path):
        """With sub-issues linked, C2 advances even if parent has no milestone object."""
        store = _store(tmp_path)
        child = _child_issue(101, state="OPEN", labels=["issuesmith:draft-done"], milestone=None)
        parent = _parent_issue(milestone=None)
        client = FakeClient(
            issues={100: parent, 101: child},
            sub_issues={100: [child]},
        )
        advance_milestone_chains(store, client, _chain_config())
        snap = store.snapshot()
        phases = {snap.requests[rid].phase for rid in snap.active_order}
        assert "develop" in phases


    def test_list_open_milestones_still_available_as_deprecated(self):
        """Deprecated function remains and returns a scope:milestone label search."""
        parent = _parent_issue()
        client = FakeClient(issues={100: parent})
        found = _list_open_milestones(client)
        assert len(found) == 1
        assert found[0]["number"] == 100


class TestSub1PublicApi:
    """SUB1 parser/body/preflight public API on issuesmith.milestone (#4276)."""

    def test_plan_section_returns_split_plan_body(self):
        body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | child | `sumipan/nexus` | scope | {NONE} |\n"
        )
        section = plan_section(body)
        assert section is not None
        assert "Sub-issue Plan" not in section
        assert "child" in section


    def test_parse_split_plan_raises_on_dotted_number(self):
        """Refs sumipan/issuesmith#8178 — dotted number column stops with SplitPlanError."""
        body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1. | child | `sumipan/nexus` | scope | {NONE} |\n"
        )
        with pytest.raises(SplitPlanError) as exc:
            parse_split_plan(body, parent_target_repo="sumipan/nexus")
        assert exc.value.violations
        assert exc.value.violations[0].startswith("split plan row 1:")

    def test_parse_split_plan_raises_on_short_row(self):
        """Refs sumipan/issuesmith#8178 — row with too few cells raises SplitPlanError."""
        body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            "| 1 |\n"
        )
        with pytest.raises(SplitPlanError) as exc:
            parse_split_plan(body, parent_target_repo="sumipan/nexus")
        assert exc.value.violations[0].startswith("split plan row 1:")

    def test_parse_split_plan_skips_blank_row(self):
        """Refs sumipan/issuesmith#8178 — blank rows are skipped; valid rows still parse."""
        body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            "|   |   |   |   |   |\n"
            f"| 1 | first | `sumipan/nexus` | scope a | {NONE} |\n"
            f"| 2 | second | `sumipan/nexus` | scope b | {NONE} |\n"
        )
        rows, has_repo = parse_split_plan(body, parent_target_repo="sumipan/nexus")
        assert has_repo is True
        assert [r.row_num for r in rows] == [1, 2]

    def test_build_child_body_preserves_target_repo_and_ac(self):
        parent_body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | child | `sumipan/nexus` | scope | {NONE} |\n"
        )
        row = PlanRow(row_num=1, title="child", repo="sumipan/nexus", scope="scope", dep_raw=NONE)
        paths = allow_paths_for_row(parent_body, row)
        child = build_child_body(
            parent_body=parent_body,
            parent_number=100,
            row=row,
            resolved_dep=NONE,
            client=MagicMock(),
            parent_labels=["scope:milestone"],
            allow_paths=paths,
        )
        assert "target_repo: sumipan/nexus" in child
        assert "#100" in child
        assert child.count("##") >= 3

