"""Tests for milestone chain rules C0–C4."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from issuesmith.config import MilestoneChainConfig, reset_config_cache
from issuesmith.milestone import (
    advance_milestone_chains,
    ensure_sub1_binding,
    link_sub_issue,
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


class _ApiError(Exception):
    """Minimal stand-in for ghdag GitHubApiError (status_code attribute)."""

    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class FakeClient:
    def __init__(
        self,
        issues=None,
        children=None,
        comments=None,
        *,
        add_sub_issue_error: Exception | None = None,
        add_sub_issue_return=None,
    ):
        self.issues = issues or {}
        self.children = children or {}
        self.comments = comments or {}
        self.posted_comments: list[tuple[int, str]] = []
        self.closed: list[int] = []
        self.label_ops: list[tuple[str, int, object]] = []
        self.sub_issue_links: list[tuple[int, int]] = []
        self.add_sub_issue_error = add_sub_issue_error
        self.add_sub_issue_return = add_sub_issue_return

    def issue_get(self, number, fields=None):
        if number not in self.issues:
            raise RuntimeError(f"missing issue #{number}")
        return dict(self.issues[number])

    def get_issue_comments(self, number):
        return list(self.comments.get(number, []))

    def issue_comment(self, number, body):
        self.posted_comments.append((number, body))
        self.comments.setdefault(number, []).append({"body": body})

    def issue_close(self, number):
        self.closed.append(number)
        if number in self.issues:
            self.issues[number] = dict(self.issues[number])
            self.issues[number]["state"] = "CLOSED"

    def add_labels(self, number, labels):
        self.label_ops.append(("add", number, labels))

    def remove_labels(self, number, labels):
        self.label_ops.append(("remove", number, labels))

    def add_sub_issue(self, parent_number, child_id):
        if self.add_sub_issue_error is not None:
            raise self.add_sub_issue_error
        self.sub_issue_links.append((parent_number, child_id))
        return self.add_sub_issue_return

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
        failure = result.results[0].failures[0]
        assert "V1" in failure
        assert "sumipan/nexus" in failure
        assert "sumipan/other" in failure


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

    def test_c0_releases_in_flight_on_draft_done(self, tmp_path, monkeypatch):
        """C0 absorbed into queue.dispatch_one draft-done release (#2980)."""
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.add_in_flight(100, "claude", role="design")
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
        monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
        result = qmod.dispatch_one(
            now=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
            client=client,
            store=store,
            skip_seed=True,
        )
        assert result.dispatched is False
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

    def test_c4_auto_closes_parent_when_all_children_merge_done(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child_101 = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        child_102 = _child_issue(102, state="CLOSED", labels=["issuesmith:merge-done"])
        parent = _parent_issue()
        client = FakeClient(
            issues={100: parent, 101: child_101, 102: child_102},
            children={1: [child_101, child_102]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert len(client.posted_comments) == 1
        assert client.closed == [100]
        chain = store.get_milestone_chain(100)
        assert chain.get("notified_all_done") is True
        assert chain.get("closed_parent") is True
        assert client.label_ops == []
        # Parent labels unchanged (no add/remove on close).
        assert [label["name"] for label in client.issues[100]["labels"]] == [
            "scope:milestone",
            DONE_LABEL["draft"],
            DONE_LABEL["sub"],
        ]

    def test_c4_auto_close_is_idempotent_on_retick(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child_101 = _child_issue(101, state="CLOSED", labels=["issuesmith:merge-done"])
        child_102 = _child_issue(102, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child_101, 102: child_102},
            children={1: [child_101, child_102]},
        )
        advance_milestone_chains(store, client, _chain_config())
        advance_milestone_chains(store, client, _chain_config())
        assert len(client.posted_comments) == 1
        assert client.closed == [100]

    def test_c4_halts_when_child_closed_without_merge_done(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        child_101 = _child_issue(101, state="CLOSED", labels=["issuesmith:rejected"])
        child_102 = _child_issue(102, state="CLOSED", labels=["issuesmith:merge-done"])
        client = FakeClient(
            issues={100: _parent_issue(), 101: child_101, 102: child_102},
            children={1: [child_101, child_102]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert client.closed == []
        assert len(client.posted_comments) == 1
        assert "確認待ち: #101 が merge-done 以外で終了" in client.posted_comments[0][1]
        chain = store.get_milestone_chain(100)
        assert chain.get("stage") == "halted"
        assert "child closed without merge-done: #101" in chain.get("halted_reason", "")

    def test_c4_resume_reevaluates_after_halt(self, tmp_path):
        store = _store(tmp_path)
        store.update_milestone_chain(100, {"stage": "children_validated"})
        # draft-done を残し、resume 後の C2 検証を通したうえで C4 を再評価させる。
        child_101 = _child_issue(
            101,
            state="CLOSED",
            labels=["issuesmith:draft-done", "issuesmith:rejected"],
        )
        child_102 = _child_issue(
            102,
            state="CLOSED",
            labels=["issuesmith:draft-done", "issuesmith:merge-done"],
        )
        client = FakeClient(
            issues={100: _parent_issue(), 101: child_101, 102: child_102},
            children={1: [child_101, child_102]},
        )
        advance_milestone_chains(store, client, _chain_config())
        assert store.get_milestone_chain(100).get("stage") == "halted"
        assert store.resume_milestone_chain(100) is True
        advance_milestone_chains(store, client, _chain_config())
        # Still without merge-done → halt again; no close; comment stays once.
        assert store.get_milestone_chain(100).get("stage") == "halted"
        assert "child closed without merge-done: #101" in store.get_milestone_chain(100).get(
            "halted_reason", ""
        )
        assert client.closed == []
        assert len(client.posted_comments) == 1

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
        assert "親の close は人間が行う" in client.posted_comments[0][1]
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


def test_dependency_table_index_column_with_resolved_ref_is_not_plan_ref():
    """`| # | 依存先 | 状態 |` の連番列を plan ref と誤判定しない（2026-09-10、#3000 実測）。"""
    from issuesmith.milestone import _dependency_refs_unresolved

    body = (
        "親イシュー: #2934\n依存: #2999\n\n"
        "## 依存（先行）\n\n"
        "| # | 依存先 | 状態 |\n"
        "|---|--------|------|\n"
        "| 1 | #2999 (委譲ジョブの進捗をスレッドに逐次表示する) | OPEN |\n\n\n"
        "## スコープ\n本文\n"
    )
    assert _dependency_refs_unresolved(body) == []


def test_dependency_table_bare_plan_ref_is_still_unresolved():
    from issuesmith.milestone import _dependency_refs_unresolved

    body = "## 依存（先行）\n\n| # | 依存先 |\n|---|---|\n| 1 | サブ1 |\n"
    assert _dependency_refs_unresolved(body) == ["unresolved plan ref #1 in dependency table"]


class TestLinkSubIssue:
    def test_link_sub_issue_success_sets_sub_issue_link(self):
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {"number": 100, "state": "OPEN", "labels": [], "milestone": {"number": 1}}
        client = FakeClient(issues={100: parent, 101: child})
        assert link_sub_issue(client, 100, 101) is True
        assert client.sub_issue_links == [(100, 5407000506)]

    def test_link_sub_issue_422_duplicate_is_idempotent(self):
        """ghdag add_sub_issue treats 422 as success (returns None); link stays True."""
        child = {"number": 101, "id": 5407000506, "state": "OPEN", "labels": []}
        parent = {"number": 100, "state": "OPEN", "labels": [], "milestone": {"number": 1}}
        client = FakeClient(
            issues={100: parent, 101: child},
            add_sub_issue_return=None,
        )
        assert link_sub_issue(client, 100, 101) is True
        assert client.sub_issue_links == [(100, 5407000506)]

    def test_link_sub_issue_422_raised_is_still_idempotent(self):
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
        """#3059: milestone 未設定でもサブイシューリンク成功なら SUB1 は停止しない。"""
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
        """milestone 未設定かつリンク失敗 → エラーコメントを投稿して False。"""
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
        assert "milestone 未設定" in body
        assert "<!-- issuesmith:sub1:no-milestone-no-sub-link -->" in body

    def test_ensure_sub1_binding_with_milestone_continues_even_if_link_fails(self):
        """従来 milestone 経路が生きていればリンク失敗でも続行（握りつぶし）。"""
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
