"""Milestone chain: sub-issue linking, enumeration and SUB1 public API."""

from __future__ import annotations

from unittest.mock import MagicMock

from issuesmith.milestone import (
    PlanRow,
    _list_open_milestones,
    advance_milestone_chains,
    allow_paths_for_row,
    build_child_body,
    ensure_sub1_binding,
    link_sub_issue,
    milestone_status,
    parse_split_plan,
    plan_section,
    prevalidate_child_body,
)
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

    def test_link_sub_issue_duplicate_http_422_is_idempotent(self):
        """ghdag add_sub_issue treats 422 as success (returns None); link stays True."""
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {"number": 100, "state": "OPEN", "labels": [], "milestone": {"number": 1}}
        client = FakeClient(
            issues={100: parent, 101: child},
            add_sub_issue_return=None,
        )
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

    def test_link_sub_issue_api_error_swallows_and_logs(self, capsys):
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {"number": 100, "state": "OPEN", "labels": [], "milestone": {"number": 1}}
        client = FakeClient(
            issues={100: parent, 101: child},
            add_sub_issue_error=_ApiError("boom", status_code=500),
        )
        assert link_sub_issue(client, 100, 101) is False
        assert client.sub_issue_links == []
        err = capsys.readouterr().err
        assert "link_sub_issue" in err
        assert "100" in err
        assert "101" in err

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

    def test_ensure_sub1_binding_with_milestone_continues_even_if_link_fails(self):
        """If the legacy milestone path is alive, continue even when link fails (swallowed)."""
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {
            "number": 100,
            "state": "OPEN",
            "labels": [{"name": "scope:milestone"}],
            "milestone": {"number": 1},
        }
        client = FakeClient(
            issues={100: parent, 101: child},
            add_sub_issue_error=_ApiError("boom", status_code=500),
        )
        assert ensure_sub1_binding(client, 100, 101) is True
        assert client.posted_comments == []


class TestSubIssuesEnumeration:
    """#3127: enumerate children via list_sub_issues, with milestone fallback."""

    def test_list_sub_issues_enumerates_linked_children(self, tmp_path):
        store = _store(tmp_path)
        child = _child_issue(101, state="OPEN", labels=["issuesmith:draft-done"])
        parent = _parent_issue()
        client = FakeClient(
            issues={100: parent, 101: child},
            sub_issues={100: [child]},
            children={1: []},  # empty milestone enum; still advance via sub_issues
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.list_sub_issues_calls == [100]
        assert not any("milestone=" in path for path in client.api_request_calls)
        snap = store.snapshot()
        phases = {snap.requests[rid].phase for rid in snap.active_order}
        assert "develop" in phases

    def test_fallback_to_milestone_children_when_sub_issues_empty(self, tmp_path):
        store = _store(tmp_path)
        child = _child_issue(101, state="OPEN", labels=["issuesmith:draft-done"])
        parent = _parent_issue()
        client = FakeClient(
            issues={100: parent, 101: child},
            sub_issues={100: []},
            children={1: [child]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.list_sub_issues_calls == [100]
        assert any("milestone=1" in path for path in client.api_request_calls)
        snap = store.snapshot()
        phases = {snap.requests[rid].phase for rid in snap.active_order}
        assert "develop" in phases

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

    def test_candidate_parents_uses_scope_label_not_list_open_milestones(
        self, tmp_path, monkeypatch
    ):
        store = _store(tmp_path)
        child = _child_issue(101, state="OPEN", labels=["issuesmith:draft-done"])
        parent = _parent_issue()
        client = FakeClient(
            issues={100: parent, 101: child},
            sub_issues={100: [child]},
        )

        def _boom(*_a, **_k):
            raise AssertionError("_list_open_milestones must not be called")

        monkeypatch.setattr(
            "issuesmith.milestone._list_open_milestones",
            _boom,
        )
        advance_milestone_chains(store, client, _chain_config())
        snap = store.snapshot()
        phases = {snap.requests[rid].phase for rid in snap.active_order}
        assert "develop" in phases
        assert any("labels=scope:milestone" in path for path in client.api_request_calls)

    def test_c4_auto_close_via_sub_issues(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child_101 = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        child_102 = _child_issue(102, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child_101, 102: child_102},
            sub_issues={100: [child_101, child_102]},
            children={1: []},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == [100]
        assert client.list_sub_issues_calls == [100]
        assert not any("milestone=" in path for path in client.api_request_calls)

    def test_milestone_status_shows_sub_issues_summary(self, tmp_path, capsys):
        child = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        parent = _parent_issue()
        client = FakeClient(
            issues={100: parent, 101: child},
            sub_issues={100: [child]},
            summaries={100: {"total": 1, "completed": 1, "percent_completed": 100}},
        )
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        code = milestone_status(100, client=client, store=store)
        assert code == 0
        out = capsys.readouterr().out
        assert "sub_issues_summary:" in out
        assert "total=1" in out
        assert "completed=1" in out
        assert "percent_completed=100" in out
        assert "101" in out
        assert "chain:" in out
        assert client.list_sub_issues_calls.count(100) >= 1
        assert all(c == 100 for c in client.list_sub_issues_calls)

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

    def test_parse_split_plan_structures_row_and_repo(self):
        body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | child | `sumipan/nexus` | scope text | {NONE} |\n"
        )
        rows, has_repo = parse_split_plan(body, parent_target_repo="sumipan/nexus")
        assert has_repo is True
        assert len(rows) == 1
        assert rows[0] == PlanRow(
            row_num=1,
            title="child",
            repo="sumipan/nexus",
            scope="scope text",
            dep_raw=NONE,
        )

    def test_parse_split_plan_rejects_malformed_number_column(self):
        body = (
            _PARENT_BODY
            + f"\n## Milestone\n\n### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| x | child | `sumipan/nexus` | scope | {NONE} |\n"
        )
        rows, has_repo = parse_split_plan(body, parent_target_repo="sumipan/nexus")
        assert has_repo is True
        assert rows == []

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

    def test_prevalidate_child_body_rejects_unsupported_repo(self):
        child_body = _CHILD_BODY
        failures = prevalidate_child_body(
            body=child_body,
            row_repo="sumipan/unknown",
            parent_issue_number=100,
            resolved_dep=NONE,
            client=MagicMock(),
            supported=frozenset({"sumipan/nexus"}),
        )
        assert any("V1 target_repo mismatch" in f for f in failures)
