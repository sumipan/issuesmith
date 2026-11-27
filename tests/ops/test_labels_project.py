"""Tests for ops/labels.py — phase/attention label projection, apply, reconcile (#3484)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from issuesmith.ops.labels import ExecRecord, apply, project, project_issue, reconcile

NS = 'issuesmith'

def _mk_issue(number, label_names):
    return {'number': number, 'labels': [{'name': name} for name in label_names], 'state': 'OPEN'}

def _phase_labels(label_set):
    result = set()
    for lbl in label_set:
        if not lbl.startswith(f'{NS}:'):
            continue
        suffix = lbl[len(NS) + 1:]
        if suffix == 'queued':
            continue
        for phase in ('draft', 'develop', 'merge', 'sub'):
            for status in ('ready', 'running', 'done'):
                if suffix == f'{phase}-{status}':
                    result.add(lbl)
    return result

def _attn_labels(label_set):
    return {lbl for lbl in label_set if f'{NS}:andon-' in lbl or lbl == f'{NS}:waiting'}

class TestProjectPhaseAxis:

    def test_queued_returns_queued_label(self):
        result = project(1, queue_state='queued', exec_records=[], andon_inbox=[])
        assert f'{NS}:queued' in result

    def test_queued_returns_at_most_one_phase_label(self):
        result = project(1, queue_state='queued', exec_records=[], andon_inbox=[])
        assert len(_phase_labels(result)) == 0
        assert f'{NS}:queued' in result

    def test_exec_running_returns_running_label(self):
        recs = [ExecRecord(phase='develop', status='running')]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f'{NS}:develop-running' in result
        assert f'{NS}:queued' not in result

    def test_exec_done_returns_done_label(self):
        recs = [ExecRecord(phase='draft', status='done')]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f'{NS}:draft-done' in result

    def test_at_most_one_phase_label_with_multiple_exec_records(self):
        recs = [ExecRecord(phase='draft', status='done'), ExecRecord(phase='develop', status='running')]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert len(_phase_labels(result)) <= 1

    def test_most_advanced_phase_wins(self):
        recs = [ExecRecord(phase='draft', status='done'), ExecRecord(phase='develop', status='running')]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f'{NS}:develop-running' in result
        assert f'{NS}:draft-done' not in result

    def test_done_status_beats_running_same_phase(self):
        recs = [ExecRecord(phase='develop', status='running'), ExecRecord(phase='develop', status='done')]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f'{NS}:develop-done' in result
        assert f'{NS}:develop-running' not in result

    def test_no_state_returns_no_phase_label(self):
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[])
        assert len(_phase_labels(result)) == 0

    def test_queued_marker_alone_without_exec_records(self):
        result = project(1, queue_state='queued', exec_records=[], andon_inbox=[])
        assert result == {f'{NS}:queued'}

    def test_exec_ready_returns_ready_label(self):
        recs = [ExecRecord(phase='merge', status='ready')]
        result = project(1, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f'{NS}:merge-ready' in result

class TestQueuedLabelToggle:

    def test_enqueued_issue_has_queued_label(self):
        result = project(42, queue_state='queued', exec_records=[], andon_inbox=[])
        assert f'{NS}:queued' in result

    def test_dispatched_issue_loses_queued_label(self):
        recs = [ExecRecord(phase='develop', status='ready')]
        result = project(42, queue_state=None, exec_records=recs, andon_inbox=[])
        assert f'{NS}:queued' not in result
        assert f'{NS}:develop-ready' in result

    def test_not_queued_no_queued_label(self):
        result = project(42, queue_state=None, exec_records=[], andon_inbox=[])
        assert f'{NS}:queued' not in result

class TestProjectAttentionAxis:

    def test_andon_decision_label(self):
        andon = MagicMock()
        andon.kind = 'decision'
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[andon])
        assert f'{NS}:andon-decision' in result

    def test_at_most_one_attention_label(self):
        andons = [MagicMock(kind='decision'), MagicMock(kind='broken')]
        result = project(1, queue_state=None, exec_records=[], andon_inbox=andons)
        assert len(_attn_labels(result)) <= 1

    def test_broken_priority_over_decision(self):
        andons = [MagicMock(kind='decision'), MagicMock(kind='broken')]
        result = project(1, queue_state=None, exec_records=[], andon_inbox=andons)
        assert f'{NS}:andon-broken' in result
        assert f'{NS}:andon-decision' not in result

    def test_broken_priority_over_blocked(self):
        andons = [MagicMock(kind='blocked'), MagicMock(kind='broken')]
        result = project(1, queue_state=None, exec_records=[], andon_inbox=andons)
        assert f'{NS}:andon-broken' in result

    def test_no_andon_no_attention_label(self):
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[])
        assert len(_attn_labels(result)) == 0

    def test_andon_blocked_label(self):
        andon = MagicMock()
        andon.kind = 'blocked'
        result = project(1, queue_state=None, exec_records=[], andon_inbox=[andon])
        assert f'{NS}:andon-blocked' in result

class _Forge:

    def __init__(self, labels):
        self.labels = set(labels)
        self.updates = []

    def issue_get(self, number, fields=None):
        return {'number': number, 'labels': [{'name': lb} for lb in sorted(self.labels)]}

    def issue_update(self, number, **kwargs):
        self.updates.append(kwargs)
        self.labels |= set(kwargs.get('labels_add') or [])
        self.labels -= set(kwargs.get('labels_remove') or [])

class TestApply:

    def test_no_api_call_when_already_aligned(self):
        forge = _Forge([f'{NS}:queued'])
        issue = _mk_issue(42, [f'{NS}:queued'])
        apply(forge, issue, {f'{NS}:queued'})
        assert forge.updates == []

    def test_does_not_add_unmanaged_labels(self):
        forge = _Forge([])
        issue = _mk_issue(42, [])
        apply(forge, issue, {'scope:milestone'})
        assert forge.updates == []

def _make_snap(queued_issues=None, in_flight_issues=None):
    snap = MagicMock()
    snap.in_flight = [{'issue': n} for n in in_flight_issues or []]
    active_order = []
    requests = {}
    for i, num in enumerate(queued_issues or []):
        rid = f'req-{i}'
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

    def test_no_divergence_when_labels_correct(self):
        issue = _mk_issue(10, [f'{NS}:queued'])
        client = _make_client([issue])
        snap = _make_snap(queued_issues=[10])
        with patch('issuesmith.queue_store.QueueStore') as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)
        assert divs == []

    def test_json_output(self, capsys):
        issue = _mk_issue(20, [f'{NS}:draft-done', f'{NS}:develop-running'])
        client = _make_client([issue])
        snap = _make_snap()
        with patch('issuesmith.queue_store.QueueStore') as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            reconcile(client, as_json=True)
        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1

    def test_queued_issue_keeps_phase_label(self):
        issue = _mk_issue(11, [f'{NS}:draft-done'])
        client = _make_client([issue])
        snap = _make_snap(queued_issues=[11])
        with patch('issuesmith.queue_store.QueueStore') as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)
        assert divs == [{'issue': 11, 'add': [f'{NS}:queued'], 'remove': []}]

    def test_queued_label_removed_when_not_in_queue(self):
        issue = _mk_issue(12, [f'{NS}:queued', f'{NS}:develop-ready'])
        client = _make_client([issue])
        snap = _make_snap()
        with patch('issuesmith.queue_store.QueueStore') as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=False)
        assert divs == [{'issue': 12, 'add': [], 'remove': [f'{NS}:queued']}]

    def test_andon_kinds_come_from_open_andon_comments(self):
        from issuesmith.andon import Andon, to_comment
        issue = _mk_issue(13, [f'{NS}:andon-blocked', f'{NS}:develop-running'])
        client = _make_client([issue], andon_issues=[{'number': 13}])
        broken = Andon(id='wf:13:cp2:0', kind='broken', issue=13, step='cp2', summary='x')
        client.get_issue_comments.return_value = [{'body': to_comment(broken)}]
        snap = _make_snap()
        with patch('issuesmith.queue_store.QueueStore') as MockStore:
            MockStore.return_value.snapshot.return_value = snap
            divs = reconcile(client, fix=True)
        assert divs == [{'issue': 13, 'add': [f'{NS}:andon-broken'], 'remove': [f'{NS}:andon-blocked']}]
        client.issue_update.assert_called_once_with(13, labels_add=[f'{NS}:andon-broken'], labels_remove=[f'{NS}:andon-blocked'])

class TestProjectIssue:

    def test_no_change_no_write(self):
        forge = _Forge({f'{NS}:develop-running'})
        assert project_issue(forge, 7, lambda st: st) == ([], [])
        assert forge.updates == []

    def test_forge_error_is_swallowed(self, capsys):
        from dataclasses import replace
        client = MagicMock()
        client.issue_get.return_value = {'labels': []}
        client.issue_update.side_effect = RuntimeError('boom')
        assert project_issue(client, 7, lambda st: replace(st, waiting=True)) == ([], [])
        assert 'WARNING' in capsys.readouterr().err


def test_github_failure_is_structured_json():
    """Refs #8129 — label hygiene reports FAILED in structured JSON with rc=1."""
    from ghdag.forge import GitHubApiError

    from issuesmith.ops.labels import run_hygiene

    client = MagicMock()
    client.issue_get.side_effect = GitHubApiError("api down", status_code=500)
    rc, payload = run_hygiene(99, client=client)
    assert rc == 1
    assert payload["error"]["code"] == "FAILED"
    assert payload["error"]["message"]
    assert payload["error"]["hint"]
