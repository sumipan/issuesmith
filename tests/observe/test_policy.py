"""tests/observe/test_policy.py -- policy evaluation and execution tests (AC-2, AC-5, AC-7)."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock

from issuesmith.config import ObserveConfig
from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    DagDeferredEvent,
    DagTerminatedEvent,
    ForgeUnavailableEvent,
    IssueStallEvent,
    LabelDriftEvent,
    SystemicStepFailureEvent,
)
from issuesmith.observe.policy import (
    AndonAction,
    HaltAction,
    ReleaseInFlightAction,
    RemoveRunningLabelAction,
    ResumeAction,
    WaitAction,
    evaluate,
    execute,
)
from issuesmith.queue_store import QueueStore


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


class TestEvaluate:
    def test_systemic_step_failure_produces_halt_and_andon(self):
        cfg_obs = ObserveConfig()
        event = SystemicStepFailureEvent(
            step="cp2",
            failure_class="ValueError",
            issues=(100, 101),
        )
        actions = evaluate([event], cfg_obs)
        halt_actions = [a for a in actions if isinstance(a, HaltAction)]
        andon_actions = [a for a in actions if isinstance(a, AndonAction)]
        assert len(halt_actions) == 1
        assert halt_actions[0].scope == "phase:develop"
        assert halt_actions[0].event_kind == "systemic_step_failure"
        assert len(andon_actions) == 1
        assert andon_actions[0].kind == "broken"

    def test_systemic_failure_with_unknown_step_halts_all(self):
        cfg_obs = ObserveConfig()
        event = SystemicStepFailureEvent(
            step="unknown-step-xyz",
            failure_class="RuntimeError",
            issues=(200, 201),
        )
        actions = evaluate([event], cfg_obs)
        halt_actions = [a for a in actions if isinstance(a, HaltAction)]
        assert halt_actions[0].scope == "all"

    def test_issue_stall_produces_andon_blocked(self):
        cfg_obs = ObserveConfig()
        event = IssueStallEvent(issue=300, phase="develop", minutes=150)
        actions = evaluate([event], cfg_obs)
        andon_actions = [a for a in actions if isinstance(a, AndonAction)]
        assert len(andon_actions) == 1
        assert andon_actions[0].kind == "blocked"

    def test_label_drift_produces_wait(self):
        cfg_obs = ObserveConfig()
        event = LabelDriftEvent(issue=400, add=("ns:queued",), remove=())
        actions = evaluate([event], cfg_obs)
        wait_actions = [a for a in actions if isinstance(a, WaitAction)]
        assert len(wait_actions) == 1

    def test_forge_unavailable_produces_halt_all_and_andon_broken(self):
        cfg_obs = ObserveConfig()
        event = ForgeUnavailableEvent(consecutive=5)
        actions = evaluate([event], cfg_obs)
        halt_actions = [a for a in actions if isinstance(a, HaltAction)]
        andon_actions = [a for a in actions if isinstance(a, AndonAction)]
        assert halt_actions[0].scope == "all"
        assert halt_actions[0].event_kind == "forge_unavailable"
        assert andon_actions[0].kind == "broken"

    def test_all_engines_paused_produces_wait(self):
        cfg_obs = ObserveConfig()
        event = AllEnginesPausedEvent(roles=("design", "implementation"))
        actions = evaluate([event], cfg_obs)
        wait_actions = [a for a in actions if isinstance(a, WaitAction)]
        assert len(wait_actions) == 1

    def test_empty_events_produces_no_actions(self):
        cfg_obs = ObserveConfig()
        assert evaluate([], cfg_obs) == []


class TestEvaluateDagDeferred:
    def test_dag_deferred_produces_wait_only(self):
        cfg_obs = ObserveConfig()
        event = DagDeferredEvent(
            issue=4909,
            key="issuesmith:impl:4909",
            step="p2",
            uuid="p2",
        )
        actions = evaluate([event], cfg_obs)
        assert len(actions) == 1
        assert isinstance(actions[0], WaitAction)
        assert not any(isinstance(a, AndonAction) for a in actions)
        assert not any(isinstance(a, RemoveRunningLabelAction) for a in actions)

    def test_dag_deferred_serializes_to_json(self):
        event = DagDeferredEvent(
            issue=4909,
            key="issuesmith:impl:4909",
            step="cp2",
            uuid="cp2-uuid",
        )
        payload = asdict(event)
        text = json.dumps(payload)
        parsed = json.loads(text)
        assert parsed["kind"] == "dag_deferred"
        assert parsed["issue"] == 4909
        assert parsed["step"] == "cp2"
        assert parsed["uuid"] == "cp2-uuid"


class TestEvaluateDagTerminated:
    def test_open_pr_retains_in_flight_via_remove_running_label(self):
        """#3895 AC-1: DagTerminatedEvent must not release in_flight when PR is open."""
        cfg_obs = ObserveConfig()
        event = DagTerminatedEvent(
            issue=3781,
            key="issuesmith:impl:3781",
            phase="develop",
            failed_step="m1",
            failed_uuid="m1-uuid",
            result_path="/jobs/done/m1",
        )
        actions = evaluate([event], cfg_obs)
        assert not any(isinstance(a, ReleaseInFlightAction) for a in actions)
        assert any(isinstance(a, RemoveRunningLabelAction) for a in actions)

    def test_no_open_pr_also_retains_in_flight(self):
        """#3895 AC-2 / #4137: DagTerminatedEvent never releases in_flight."""
        cfg_obs = ObserveConfig()
        event = DagTerminatedEvent(
            issue=3782,
            key="issuesmith:impl:3782",
            phase="develop",
            failed_step="p3",
        )
        actions = evaluate([event], cfg_obs)
        assert not any(isinstance(a, ReleaseInFlightAction) for a in actions)
        assert any(isinstance(a, RemoveRunningLabelAction) for a in actions)


class TestExecuteReleaseInFlight:
    def test_skips_release_when_open_linked_pr(self, tmp_path):
        store = _store(tmp_path)
        store.add_in_flight(
            3781,
            "claude",
            role="implementation",
            allow_paths=("tools/asana/tasksmith.py",),
            target_repo="sumipan/issuesmith",
        )
        client = MagicMock()
        client.pr_list.return_value = [
            {
                "number": 3892,
                "title": "tasksmith fix",
                "body": "Refs #3781",
                "head": {"ref": "issue-3781-abc123"},
            }
        ]
        execute(
            [ReleaseInFlightAction(issue=3781, phase="develop", reason="test")],
            store,
            sinks=[],
            client=client,
        )
        assert any(e.get("issue") == 3781 for e in store.snapshot().in_flight)

    def test_skips_release_on_normalized_pr_list_shape(self, tmp_path):
        """GitHubClient.pr_list returns headRefName and no body; no per-PR fetch."""
        store = _store(tmp_path)
        store.add_in_flight(3781, "claude", role="implementation")
        client = MagicMock()
        client.pr_list.return_value = [
            {"number": 158, "title": "unrelated", "headRefName": "feat/issue-1-x"},
            {"number": 159, "title": "fix", "headRefName": "issue-3781-abc123"},
        ]
        execute(
            [ReleaseInFlightAction(issue=3781, phase="develop", reason="test")],
            store,
            sinks=[],
            client=client,
        )
        assert any(e.get("issue") == 3781 for e in store.snapshot().in_flight)
        client.pr_get.assert_not_called()

    def test_releases_when_no_open_linked_pr(self, tmp_path):
        store = _store(tmp_path)
        store.add_in_flight(3782, "claude", role="implementation")
        client = MagicMock()
        client.pr_list.return_value = []
        execute(
            [ReleaseInFlightAction(issue=3782, phase="develop", reason="test")],
            store,
            sinks=[],
            client=client,
        )
        assert not any(e.get("issue") == 3782 for e in store.snapshot().in_flight)


class TestExecute:
    def test_halt_action_calls_set_halt(self, tmp_path):
        store = _store(tmp_path)
        action = HaltAction(scope="phase:develop", reason="systemic failure", event_kind="systemic_step_failure")
        execute([action], store, sinks=[])
        snap = store.snapshot()
        assert snap.halt is True
        assert snap.halt_scope == "phase:develop"
        assert snap.halt_event == "systemic_step_failure"

    def test_resume_action_calls_clear_halt(self, tmp_path):
        store = _store(tmp_path)
        store.set_halt(True, "some reason", scope="all", event="test")
        action = ResumeAction(reason="resolved")
        execute([action], store, sinks=[])
        snap = store.snapshot()
        assert snap.halt is False
        assert snap.halt_scope == "all"
        assert snap.halt_event is None

    def test_andon_action_calls_sink_emit(self, tmp_path):
        store = _store(tmp_path)
        sink = MagicMock()
        action = AndonAction(kind="broken", issue=100, summary="test broken")
        execute([action], store, sinks=[sink])
        sink.emit.assert_called_once()
        emitted = sink.emit.call_args[0][0]
        assert emitted.kind == "broken"
        assert emitted.issue == 100

    def test_wait_action_does_nothing(self, tmp_path):
        store = _store(tmp_path)
        action = WaitAction(reason="just wait")
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

    def test_config_loaded_from_yaml_observe_section(self, tmp_path):
        import yaml

        from issuesmith.config import load_config

        yaml_content = {
            "repo": "owner/test-repo",
            "observe": {
                "stall_minutes": 45,
                "systemic_min_issues": 3,
                "systemic_window_minutes": 90,
                "forge_max_consecutive_errors": 5,
                "task_timeout_minutes": 60,
            },
        }
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(yaml.dump(yaml_content), encoding="utf-8")

        cfg = load_config(cfg_path)
        assert cfg.observe.stall_minutes == 45
        assert cfg.observe.systemic_min_issues == 3
        assert cfg.observe.systemic_window_minutes == 90
        assert cfg.observe.forge_max_consecutive_errors == 5
        assert cfg.observe.task_timeout_minutes == 60

    def test_evaluate_uses_config_threshold_for_stall(self):
        from issuesmith.observe.events import IssueStallEvent
        from issuesmith.observe.policy import evaluate

        cfg_obs = ObserveConfig(stall_minutes=60)
        event = IssueStallEvent(issue=100, phase="develop", minutes=50)
        actions = evaluate([event], cfg_obs)
        assert len(actions) == 1
        assert isinstance(actions[0], AndonAction)
