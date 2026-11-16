"""tests/observe/test_policy.py -- policy evaluation and execution tests (AC-2, AC-5, AC-7)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from issuesmith.config import ObserveConfig
from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    IssueStallEvent,
    LabelDriftEvent,
    OrphanExecEvent,
    SystemicStepFailureEvent,
)
from issuesmith.observe.policy import (
    AndonAction,
    HaltAction,
    ReleaseInFlightAction,
    WaitAction,
    evaluate,
    execute,
)
from issuesmith.queue_store import QueueStore


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(queue_path=tmp_path / 'queue.jsonl', state_path=tmp_path / 'state.json', lock_path=tmp_path / 'lock')

class TestEvaluate:

    def test_systemic_failure_with_unknown_step_halts_all(self):
        cfg_obs = ObserveConfig()
        event = SystemicStepFailureEvent(step='unknown-step-xyz', failure_class='RuntimeError', issues=(200, 201))
        actions = evaluate([event], cfg_obs)
        halt_actions = [a for a in actions if isinstance(a, HaltAction)]
        assert halt_actions[0].scope == 'all'

    def test_issue_stall_returns_wait_only(self):
        """#6328: stall is observed via WaitAction, not an andon."""
        cfg_obs = ObserveConfig()
        event = IssueStallEvent(issue=300, phase='develop', minutes=150)
        actions = evaluate([event], cfg_obs)
        assert len(actions) == 1
        assert isinstance(actions[0], WaitAction)
        assert actions[0].reason
        assert not any((isinstance(a, AndonAction) for a in actions))

    def test_orphan_exec_returns_wait_only(self):
        """#6328: orphan exec is observed via WaitAction, not an andon."""
        cfg_obs = ObserveConfig()
        event = OrphanExecEvent(uuid='dead-beef', issue=301)
        actions = evaluate([event], cfg_obs)
        assert len(actions) == 1
        assert isinstance(actions[0], WaitAction)
        assert actions[0].reason
        assert not any((isinstance(a, AndonAction) for a in actions))

    def test_label_drift_produces_wait(self):
        cfg_obs = ObserveConfig()
        event = LabelDriftEvent(issue=400, add=('ns:queued',), remove=())
        actions = evaluate([event], cfg_obs)
        wait_actions = [a for a in actions if isinstance(a, WaitAction)]
        assert len(wait_actions) == 1

    def test_all_engines_paused_produces_wait(self):
        cfg_obs = ObserveConfig()
        event = AllEnginesPausedEvent(roles=('design', 'implementation'))
        actions = evaluate([event], cfg_obs)
        wait_actions = [a for a in actions if isinstance(a, WaitAction)]
        assert len(wait_actions) == 1

    def test_empty_events_produces_no_actions(self):
        cfg_obs = ObserveConfig()
        assert evaluate([], cfg_obs) == []

class TestExecuteReleaseInFlight:

    def test_skips_release_when_open_linked_pr(self, tmp_path):
        store = _store(tmp_path)
        store.add_in_flight(3781, 'claude', role='implementation', allow_paths=('tools/asana/tasksmith.py',), target_repo='sumipan/issuesmith')
        client = MagicMock()
        client.pr_list.return_value = [{'number': 3892, 'title': 'tasksmith fix', 'body': 'Refs #3781', 'head': {'ref': 'issue-3781-abc123'}}]
        execute([ReleaseInFlightAction(issue=3781, phase='develop', reason='test')], store, sinks=[], client=client)
        assert any((e.get('issue') == 3781 for e in store.snapshot().in_flight))

    def test_skips_release_on_normalized_pr_list_shape(self, tmp_path):
        """GitHubClient.pr_list returns headRefName and no body; no per-PR fetch."""
        store = _store(tmp_path)
        store.add_in_flight(3781, 'claude', role='implementation')
        client = MagicMock()
        client.pr_list.return_value = [{'number': 158, 'title': 'unrelated', 'headRefName': 'feat/issue-1-x'}, {'number': 159, 'title': 'fix', 'headRefName': 'issue-3781-abc123'}]
        execute([ReleaseInFlightAction(issue=3781, phase='develop', reason='test')], store, sinks=[], client=client)
        assert any((e.get('issue') == 3781 for e in store.snapshot().in_flight))
        client.pr_get.assert_not_called()

    def test_releases_when_no_open_linked_pr(self, tmp_path):
        store = _store(tmp_path)
        store.add_in_flight(3782, 'claude', role='implementation')
        client = MagicMock()
        client.pr_list.return_value = []
        execute([ReleaseInFlightAction(issue=3782, phase='develop', reason='test')], store, sinks=[], client=client)
        assert not any((e.get('issue') == 3782 for e in store.snapshot().in_flight))

class TestExecute:

    def test_wait_action_does_nothing(self, tmp_path):
        store = _store(tmp_path)
        action = WaitAction(reason='just wait')
        execute([action], store, sinks=[])
        snap = store.snapshot()
        assert snap.halt is False

class TestObserveConfigOverrides:

    def test_stall_minutes_overrides_default(self):
        cfg = ObserveConfig(stall_minutes=30)
        assert cfg.stall_minutes == 30

    def test_systemic_min_issues_overrides_default(self):
        cfg = ObserveConfig(systemic_min_issues=3)
        assert cfg.systemic_min_issues == 3

    def test_systemic_window_minutes_overrides_default(self):
        cfg = ObserveConfig(systemic_window_minutes=120)
        assert cfg.systemic_window_minutes == 120

    def test_forge_max_consecutive_errors_overrides_default(self):
        cfg = ObserveConfig(forge_max_consecutive_errors=5)
        assert cfg.forge_max_consecutive_errors == 5

    def test_task_timeout_minutes_overrides_default(self):
        cfg = ObserveConfig(task_timeout_minutes=60)
        assert cfg.task_timeout_minutes == 60
