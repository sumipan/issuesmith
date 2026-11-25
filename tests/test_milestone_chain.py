"""Tests for milestone chain rules C0–C4."""

from __future__ import annotations

import pytest

from issuesmith.config import MilestoneChainConfig, reset_config_cache
from issuesmith.language import EN
from issuesmith.milestone import (
    advance_milestone_chains,
    validate_children,
)
from issuesmith.queue_store import QueueStore
from issuesmith.queue_triage import DONE_LABEL
from tests.legacy_text import ADD, CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY

# Split-plan / section vocabulary comes from the language pack (EN in tests); the
# sub-header prefix and change-table columns still use the legacy test pack (conftest).
_, TITLE, TARGET_REPOSITORY, CONTENT, DEPENDENCY = EN.sub_plan_columns
NONE = EN.no_deps_word

# ASCII fixture data.
_PARENT_BODY = f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/foo/**
```

## Changed Files
| {REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |
|---|---|---|---|
| `sumipan/nexus` | `src/foo/a.py` | {ADD} | add |
"""

# ASCII fixture data.
_CHILD_BODY = f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/foo/**
```

## Changed Files
| {REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |
|---|---|---|---|
| `sumipan/nexus` | `src/foo/a.py` | {ADD} | add |
"""


def _store(tmp_path):
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _chain_config(**kwargs):
    defaults = {
        "enabled": True,
        "child_priority": "normal",
        "auto_develop": True,
        "auto_close_parent": True,
    }
    defaults.update(kwargs)
    return MilestoneChainConfig(**defaults)


def _parent_issue(**overrides):
    base = {
        "number": 100,
        "state": "OPEN",
        "labels": [
            {"name": "scope:milestone"},
            {"name": DONE_LABEL["draft"]},
            {"name": DONE_LABEL["sub"]},
        ],
        "body": _PARENT_BODY,
        "milestone": {"number": 1},
    }
    base.update(overrides)
    return base


def _child_issue(number: int, *, state: str, labels: list[str], **overrides):
    base = {
        "number": number,
        "state": state,
        "title": f"child {number}",
        "body": _CHILD_BODY,
        "milestone": {"number": 1},
        "labels": [{"name": name} for name in labels],
    }
    base.update(overrides)
    return base


class FakeClient:
    def __init__(
        self,
        issues=None,
        children=None,
        comments=None,
        *,
        sub_issues=None,
        summaries=None,
        add_sub_issue_error: Exception | None = None,
        add_sub_issue_return=None,
    ):
        self.issues = issues or {}
        self.children = children or {}
        self.comments = comments or {}
        self.sub_issues = sub_issues or {}
        self.summaries = summaries or {}
        self.posted_comments: list[tuple[int, str]] = []
        self.closed: list[int] = []
        self.close_reasons: dict[int, str] = {}
        self.label_ops: list[tuple[str, int, object]] = []
        self.sub_issue_links: list[tuple[int, int]] = []
        self.list_sub_issues_calls: list[int] = []
        self.api_request_calls: list[str] = []
        self.issue_get_calls: list[int] = []
        self.get_issue_comments_calls: list[int] = []
        self.add_sub_issue_error = add_sub_issue_error
        self.add_sub_issue_return = add_sub_issue_return

    def issue_get(self, number, fields=None):
        self.issue_get_calls.append(number)
        if number not in self.issues:
            raise RuntimeError(f"missing issue #{number}")
        return dict(self.issues[number])

    def get_issue_comments(self, number):
        self.get_issue_comments_calls.append(number)
        return list(self.comments.get(number, []))

    def issue_comment(self, number, body):
        self.posted_comments.append((number, body))
        self.comments.setdefault(number, []).append({"body": body})

    def issue_close(self, number, *, reason="completed"):
        self.closed.append(number)
        self.close_reasons[number] = reason
        if number in self.issues:
            self.issues[number] = dict(self.issues[number])
            self.issues[number]["state"] = "CLOSED"

    def add_labels(self, number, labels):
        self.label_ops.append(("add", number, labels))

    def issue_update(self, number, *, labels_add=None, labels_remove=None, **kwargs):
        self.label_ops.append(("update", number, (labels_add, labels_remove)))
        issue = dict(self.issues[number])
        names = [lab["name"] for lab in issue.get("labels") or []]
        names = [n for n in names if n not in (labels_remove or [])]
        names += [n for n in (labels_add or []) if n not in names]
        issue["labels"] = [{"name": n} for n in names]
        self.issues[number] = issue

    def remove_labels(self, number, labels):
        self.label_ops.append(("remove", number, labels))

    def add_sub_issue(self, parent_number, child_id):
        if self.add_sub_issue_error is not None:
            raise self.add_sub_issue_error
        self.sub_issue_links.append((parent_number, child_id))
        return self.add_sub_issue_return

    def list_sub_issues(self, parent_number):
        self.list_sub_issues_calls.append(parent_number)
        return list(self.sub_issues.get(parent_number, []))

    def sub_issues_summary(self, parent_number):
        if parent_number in self.summaries:
            return dict(self.summaries[parent_number])
        linked = self.sub_issues.get(parent_number, [])
        total = len(linked)
        completed = sum(
            1 for child in linked if str(child.get("state", "")).upper() == "CLOSED"
        )
        percent = int(completed * 100 / total) if total else 0
        return {"total": total, "completed": completed, "percent_completed": percent}

    def api_request(self, path, paginate=False):
        self.api_request_calls.append(path)
        if path.startswith("issues?state=all&milestone="):
            milestone = int(path.split("milestone=")[1].split("&")[0])
            return list(self.children.get(milestone, []))
        if "labels=scope:milestone" in path:
            return [
                issue
                for issue in self.issues.values()
                if str(issue.get("state", "")).upper() == "OPEN"
                and any(
                    (label.get("name") if isinstance(label, dict) else label) == "scope:milestone"
                    for label in (issue.get("labels") or [])
                )
            ]
        return []


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


class TestValidateChildren:

    def test_validate_children_fail_target_repo(self):
        parent = {
            "number": 100,
            "body": _PARENT_BODY,
            "milestone": {"number": 1},
            "labels": [],
        }
        bad_body = _CHILD_BODY.replace("sumipan/nexus", "sumipan/other")
        child = {
            "number": 101,
            "body": bad_body,
            "milestone": {"number": 1},
            "labels": [{"name": "issuesmith:draft-done"}],
        }
        client = FakeClient(issues={101: child})
        result = validate_children(parent, [child], client=client)
        assert result.passed is False
        failure = result.results[0].failures[0]
        assert "V1" in failure
        assert "sumipan/nexus" in failure
        assert "sumipan/other" in failure


class TestChainRules:


    def test_auto_develop_false_skips_develop_enqueue(self, tmp_path):
        store = _store(tmp_path)
        child = {
            "number": 101,
            "state": "OPEN",
            "title": "child",
            "body": _CHILD_BODY,
            "milestone": {"number": 1},
            "labels": [{"name": "issuesmith:draft-done"}],
        }
        client = FakeClient(
            issues={
                100: {
                    "number": 100,
                    "state": "OPEN",
                    "labels": [
                        {"name": "scope:milestone"},
                        {"name": DONE_LABEL["draft"]},
                        {"name": DONE_LABEL["sub"]},
                    ],
                    "body": _PARENT_BODY,
                    "milestone": {"number": 1},
                },
                101: child,
            },
            children={1: [child]},
        )
        advance_milestone_chains(store, client, _chain_config(auto_develop=False))
        snap = store.snapshot()
        assert snap.active_order == []


    def test_auto_close_parent_closes_completed(self, tmp_path):
        """Refs #7962 — auto_close_parent passes completed to issue_close."""
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child},
            children={1: [child]},
        )
        advance_milestone_chains(store, client, _chain_config(auto_close_parent=True))
        assert client.closed == [100]
        assert client.close_reasons[100] == "completed"

    def test_c4_auto_close_parent_false_keeps_notify_only(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child},
            children={1: [child]},
        )
        advance_milestone_chains(store, client, _chain_config(auto_close_parent=False))
        advance_milestone_chains(store, client, _chain_config(auto_close_parent=False))
        assert len(client.posted_comments) == 1
        # ASCII fixture data.
        assert "close" in client.posted_comments[0][1]
        assert client.closed == []
        chain = store.get_milestone_chain(100)
        assert chain.get("notified_all_done") is True
        assert chain.get("closed_parent") is not True

    def test_resume_clears_halted(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "halted", "halted_reason": "validation failed"})
        assert store.resume_milestone_chain(100) is True
        assert store.get_milestone_chain(100).get("stage") == "active"

    def test_c1_is_idempotent(self, tmp_path):
        store = _store(tmp_path)
        client = FakeClient(
            issues={
                100: {
                    "number": 100,
                    "state": "OPEN",
                    "labels": [
                        {"name": "scope:milestone"},
                        {"name": DONE_LABEL["draft"]},
                    ],
                    "body": _PARENT_BODY,
                    "milestone": {"number": 1},
                }
            },
            comments={
                100: [
                    {"body": "PIPELINE_STATUS: BRUSHUP_DONE"},
                    {"body": "## CP1 checkpoint\nINTENTIONAL_HOLD: true\nCP1_STATUS: FAIL"},
                ]
            },
        )
        advance_milestone_chains(store, client, _chain_config())
        advance_milestone_chains(store, client, _chain_config())
        snap = store.snapshot()
        sub_requests = [
            snap.requests[rid]
            for rid in snap.active_order + snap.completed_request_ids
            if rid in snap.requests and snap.requests[rid].phase == "sub"
        ]
        assert len(sub_requests) == 1


def test_dependency_table_bare_plan_ref_is_still_unresolved():
    from issuesmith.milestone import _dependency_refs_unresolved

    body = "## Dependencies\n\n| # | Dependency_c5148 |\n|---|---|\n| 1 | Sub1 |\n"
    assert _dependency_refs_unresolved(body) == ["unresolved plan ref #1 in dependency table"]


