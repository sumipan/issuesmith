"""Tests for ops/labels.py — phase/attention label projection, apply, reconcile (#3484)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from issuesmith.ops.labels import ExecRecord, apply, project, reconcile

NS = "issuesmith"


def _mk_issue(number, label_names):
    return {"number": number, "labels": [{"name": name} for name in label_names], "state": "OPEN"}


def _phase_labels(label_set):
    result = set()
    for lbl in label_set:
        if not lbl.startswith(f"{NS}:"):
            continue
        suffix = lbl[len(NS) + 1:]
        if suffix == "queued":
            result.add(lbl)
            continue
        for phase in ("draft", "develop", "merge", "sub"):
            for status in ("ready", "running", "done"):
                if suffix == f"{phase}-{status}":
                    result.add(lbl)
    return result


def _attn_labels(label_set):
    return {lbl for lbl in label_set if f"{NS}:andon-" in lbl or lbl == f"{NS}:waiting"}


# ---------------------------------------------------------------------------
# project() — phase axis
# ---------------------------------------------------------------------------

class TestProjectPhaseAxis:
    def test_queued_returns_queued_label(self):
        result = project(1, queue_state="queued", exec_records=[], andon_inbox=[])
        assert f"{NS}:queued" in result

    def test_queued_returns_at_most_one_phase_label(self):
        result = project(1, queue_state="queued", exec_records=[], andon_inbox=[])
        assert len(_phase_labels(result)) == 1

    def test_exec_running_returns_running_label(self):
        recs = [ExecRecord(phase="develop", status="running")]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f"{NS}:develop-running" in result
        assert f"{NS}:queued" not in result

    def test_exec_done_returns_done_label(self):
        recs = [ExecRecord(phase="draft", status="done")]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f"{NS}:draft-done" in result

    def test_at_most_one_phase_label_with_multiple_exec_records(self):
        recs = [ExecRecord(phase="draft", status="done"), ExecRecord(phase="develop", status="running")]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert len(_phase_labels(result)) <= 1

    def test_most_advanced_phase_wins(self):
        recs = [ExecRecord(phase="draft", status="done"), ExecRecord(phase="develop", status="running")]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f"{NS}:develop-running" in result
        assert f"{NS}:draft-done" not in result

    def test_done_status_beats_running_same_phase(self):
        recs = [ExecRecord(phase="develop", status="running"), ExecRecord(phase="develop", status="done")]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f"{NS}:develop-done" in result
        assert f"{NS}:develop-running" not in result

    def test_no_state_returns_no_phase_label(self):
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[])
        assert len(_phase_labels(result)) == 0

    def test_queued_overrides_exec_records(self):
        # When queue_state="queued", the phase label is <ns>:queued regardless of exec_records
        recs = [ExecRecord(phase="develop", status="running")]
        result = project(1, queue_state="queued", exec_records=recs, andon_inbox=[])
        assert f"{NS}:queued" in result

    def test_exec_ready_returns_ready_label(self):
        recs = [ExecRecord(phase="merge", status="ready")]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f"{NS}:merge-ready" in result


# ---------------------------------------------------------------------------
# project() — queued label toggle (enqueue / dispatch)
# ---------------------------------------------------------------------------

class TestQueuedLabelToggle:
    def test_enqueued_issue_has_queued_label(self):
        result = project(42, queue_state="queued", exec_records=[], andon_inbox=[])
        assert f"{NS}:queued" in result

    def test_dispatched_issue_loses_queued_label(self):
        recs = [ExecRecord(phase="develop", status="ready")]
        result = project(42, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f"{NS}:queued" not in result
        assert f"{NS}:develop-ready" in result

    def test_not_queued_no_queued_label(self):
        result = project(42, queue_state=None, exec_records=[], andon_inbox=[])
        assert f"{NS}:queued" not in result


# ---------------------------------------------------------------------------
# project() — attention axis
# ---------------------------------------------------------------------------

class TestProjectAttentionAxis:
    def test_andon_decision_label(self):
        andon = MagicMock()
        andon.kind = "decision"
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[andon])
        assert f"{NS}:andon-decision" in result

    def test_at_most_one_attention_label(self):
        andons = [MagicMock(kind="decision"), MagicMock(kind="broken")]
        result = project(1, queue_state=None, exec_records=[], andon_inbox=andons)
        assert len(_attn_labels(result)) <= 1

    def test_broken_priority_over_decision(self):
        andons = [MagicMock(kind="decision"), MagicMock(kind="broken")]
        result = project(1, queue_state=None, exec_records=[], andon_inbox=andons)
        assert f"{NS}:andon-broken" in result
        assert f"{NS}:andon-decision" not in result

    def test_broken_priority_over_blocked(self):
        andons = [MagicMock(kind="blocked"), MagicMock(kind="broken")]
        result = project(1, queue_state=None, exec_records=[], andon_inbox=andons)
        assert f"{NS}:andon-broken" in result

    def test_no_andon_no_attention_label(self):
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[])
        assert len(_attn_labels(result)) == 0

    def test_andon_blocked_label(self):
        andon = MagicMock()
        andon.kind = "blocked"
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[andon])
        assert f"{NS}:andon-blocked" in result


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------

class TestApply:
    def test_adds_missing_managed_label(self):
        client = MagicMock()
        issue = _mk_issue(42, [])
        apply(client, issue, {f"{NS}:queued"})
        client.issue_update.assert_called_once()
        kwargs = client.issue_update.call_args[1]
        assert f"{NS}:queued" in kwargs["labels_add"]

    def test_removes_extra_managed_label(self):
        client = MagicMock()
        issue = _mk_issue(42, [f"{NS}:queued"])
        apply(client, issue, set())
        client.issue_update.assert_called_once()
        kwargs = client.issue_update.call_args[1]
        assert f"{NS}:queued" in kwargs["labels_remove"]

    def test_no_api_call_when_already_aligned(self):
        client = MagicMock()
        issue = _mk_issue(42, [f"{NS}:queued"])
        apply(client, issue, {f"{NS}:queued"})
        client.issue_update.assert_not_called()

    def test_does_not_remove_unmanaged_labels(self):
        client = MagicMock()
        issue = _mk_issue(42, ["scope:milestone", f"{NS}:queued"])
        apply(client, issue, set())
        kwargs = client.issue_update.call_args[1]
        assert "scope:milestone" not in kwargs.get("labels_remove", [])

    def test_does_not_add_unmanaged_labels(self):
        client = MagicMock()
        issue = _mk_issue(42, [])
        # passing an unmanaged label in desired should not trigger add
        apply(client, issue, {"scope:milestone"})
        # no call because scope:milestone is not managed
        client.issue_update.assert_not_called()

    def test_adds_and_removes_in_one_call(self):
        client = MagicMock()
        issue = _mk_issue(42, [f"{NS}:draft-done", f"{NS}:develop-running"])
        apply(client, issue, {f"{NS}:develop-running"})
        client.issue_update.assert_called_once()
        kwargs = client.issue_update.call_args[1]
        assert f"{NS}:draft-done" in kwargs["labels_remove"]
        assert not kwargs.get("labels_add")


# ---------------------------------------------------------------------------
# reconcile() — divergence detection
# ---------------------------------------------------------------------------

def _make_snap(queued_issues=None, in_flight_issues=None):
    snap = MagicMock()
    snap.in_flight = [{"issue": n} for n in (in_flight_issues or [])]
    active_order = []
    requests = {}
    for i, num in enumerate(queued_issues or []):
        rid = f"req-{i}"
        active_order.append(rid)
        req = MagicMock()
        req.issue = num
        requests[rid] = req
    snap.active_order = active_order
    snap.requests = requests
    return snap


def _make_client(issues, andon_issues=None):
    client = MagicMock()
    client.api_request.return_value = issues
    client.list_issues.return_value = andon_issues or []
    client.get_issue_comments.return_value = []
    return client


class TestReconcile:
    def test_detects_missing_queued_label(self):
        # Issue #10 is in queue but has no <ns>:queued label → divergence
        issue = _mk_issue(10, [])
        client = _make_client([issue])
        snap = _make_snap(queued_issues=[10])

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)

        assert len(divs) == 1
        assert divs[0]["issue"] == 10
        assert f"{NS}:queued" in divs[0]["add"]

    def test_no_divergence_when_labels_correct(self):
        # Issue #10 is queued and already has <ns>:queued
        issue = _mk_issue(10, [f"{NS}:queued"])
        client = _make_client([issue])
        snap = _make_snap(queued_issues=[10])

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)

        assert divs == []

    def test_detects_stale_phase_label(self):
        # Issue has draft-done + develop-running; draft-done is stale
        issue = _mk_issue(20, [f"{NS}:draft-done", f"{NS}:develop-running"])
        client = _make_client([issue])
        snap = _make_snap()

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)

        assert len(divs) == 1
        assert divs[0]["issue"] == 20
        assert f"{NS}:draft-done" in divs[0]["remove"]

    def test_fix_applies_label_changes(self):
        issue = _mk_issue(20, [f"{NS}:draft-done", f"{NS}:develop-running"])
        client = _make_client([issue])
        snap = _make_snap()

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            reconcile(client, fix=True)

        client.issue_update.assert_called()

    def test_json_output(self, capsys):
        issue = _mk_issue(20, [f"{NS}:draft-done", f"{NS}:develop-running"])
        client = _make_client([issue])
        snap = _make_snap()

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            reconcile(client, as_json=True)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_skips_pull_requests(self):
        # api_request returns PRs too; they should be skipped
        pr = {"number": 5, "labels": [], "pull_request": {}, "state": "OPEN"}
        client = _make_client([pr])
        snap = _make_snap()

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)

        assert divs == []

    def test_backlog_label_not_managed(self):
        # issuesmith:backlog should not be touched by project()
        issue = _mk_issue(30, [f"{NS}:backlog"])
        client = _make_client([issue])
        snap = _make_snap()

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)

        assert divs == []

    def test_migrate_done_label_not_managed(self):
        # issuesmith:migrate-done should not be touched by project()
        issue = _mk_issue(31, [f"{NS}:migrate-done"])
        client = _make_client([issue])
        snap = _make_snap()

        with patch("issuesmith.queue_store.QueueStore") as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)

        assert divs == []
