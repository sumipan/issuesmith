"""Tests for milestone chain rules C0–C4."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from issuesmith.config import MilestoneChainConfig, reset_config_cache
from issuesmith.milestone import (
    advance_milestone_chains,
    milestone_last_issue_terminal_ok,
    validate_children,
)
from issuesmith.queue_store import QueueStore
from issuesmith.queue_triage import DONE_LABEL

_NOW = datetime.now(timezone.utc).isoformat()

_PARENT_BODY = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/foo/**
```

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `src/foo/a.py` | 新規 | add |
"""

_CHILD_BODY = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/foo/**
```

## 変更対象ファイル
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `src/foo/a.py` | 新規 | add |
"""


def _store(tmp_path):
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _chain_config(**kwargs):
    defaults = {"enabled": True, "child_priority": "normal", "auto_develop": True}
    defaults.update(kwargs)
    return MilestoneChainConfig(**defaults)


class FakeClient:
    def __init__(self, issues=None, children=None, comments=None):
        self.issues = issues or {}
        self.children = children or {}
        self.comments = comments or {}
        self.posted_comments: list[tuple[int, str]] = []

    def issue_get(self, number, fields=None):
        if number not in self.issues:
            raise RuntimeError(f"missing issue #{number}")
        return dict(self.issues[number])

    def get_issue_comments(self, number):
        return list(self.comments.get(number, []))

    def issue_comment(self, number, body):
        self.posted_comments.append((number, body))

    def api_request(self, path, paginate=False):
        if path.startswith("issues?state=all&milestone="):
            milestone = int(path.split("milestone=")[1].split("&")[0])
            return list(self.children.get(milestone, []))
        if "labels=scope:milestone" in path:
            return [
                issue
                for issue in self.issues.values()
                if any(
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


class TestMilestoneHelpers:
    def test_last_issue_terminal_ok_for_milestone_parent(self):
        labels = {"scope:milestone", "issuesmith:draft-done"}
        assert milestone_last_issue_terminal_ok(labels) is True

    def test_last_issue_terminal_ok_false_without_milestone(self):
        labels = {"issuesmith:draft-done"}
        assert milestone_last_issue_terminal_ok(labels) is False


class TestValidateChildren:
    def test_validate_children_pass(self):
        parent = {
            "number": 100,
            "body": _PARENT_BODY,
            "milestone": {"number": 1},
            "labels": [{"name": "scope:milestone"}],
        }
        child = {
            "number": 101,
            "body": _CHILD_BODY,
            "milestone": {"number": 1},
            "labels": [{"name": "issuesmith:draft-done"}],
        }
        client = FakeClient(issues={101: child})
        result = validate_children(parent, [child], client=client)
        assert result.passed is True

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
        assert "V1" in result.results[0].failures[0]


class TestChainRules:
    def test_disabled_does_nothing(self, tmp_path):
        store = _store(tmp_path)
        store.add_in_flight(100, "claude")
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
            }
        )
        advance_milestone_chains(store, client, _chain_config(enabled=False))
        assert store.snapshot().in_flight

    def test_c0_releases_in_flight_on_draft_done(self, tmp_path):
        store = _store(tmp_path)
        store.add_in_flight(100, "claude")
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
            }
        )
        advance_milestone_chains(store, client, _chain_config())
        assert store.snapshot().in_flight == []

    def test_c1_enqueues_sub_after_intentional_hold(self, tmp_path):
        store = _store(tmp_path)
        client = FakeClient(
            issues={
                100: {
                    "number": 100,
                    "state": "OPEN",
                    "title": "milestone",
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
        snap = store.snapshot()
        assert len(snap.active_order) == 1
        req = snap.requests[snap.active_order[0]]
        assert req.phase == "sub"
        assert req.source == "milestone-chain"

    def test_c2_zero_children_halts(self, tmp_path):
        store = _store(tmp_path)
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
                }
            },
            children={1: []},
        )
        advance_milestone_chains(store, client, _chain_config())
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert client.posted_comments

    def test_c2_validation_fail_halts(self, tmp_path):
        store = _store(tmp_path)
        child = {
            "number": 101,
            "state": "OPEN",
            "title": "child",
            "body": _CHILD_BODY.replace("sumipan/nexus", "sumipan/other"),
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
        advance_milestone_chains(store, client, _chain_config())
        assert store.get_milestone_chain(100).get("stage") == "halted"

    def test_c2_pass_enqueues_develop(self, tmp_path):
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
        advance_milestone_chains(store, client, _chain_config())
        snap = store.snapshot()
        phases = {snap.requests[rid].phase for rid in snap.active_order}
        assert "develop" in phases

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

    def test_c4_notifies_once_when_children_terminal(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child = {
            "number": 101,
            "state": "CLOSED",
            "title": "child",
            "body": _CHILD_BODY,
            "milestone": {"number": 1},
            "labels": [{"name": "issuesmith:merge-done"}],
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
        advance_milestone_chains(store, client, _chain_config())
        advance_milestone_chains(store, client, _chain_config())
        assert len(client.posted_comments) == 1
        assert store.get_milestone_chain(100).get("notified_all_done") is True

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
