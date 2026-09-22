"""tests/observe/test_policy.py -- policy evaluation and execution tests (AC-2, AC-5, AC-7)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from issuesmith.config import ObserveConfig, get_config
from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    ForgeUnavailableEvent,
    LabelDriftEvent,
    ObserveEvent,
    SystemicStepFailureEvent,
    IssueStallEvent,
)
from issuesmith.observe.policy import (
    AndonAction,
    HaltAction,
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
        event = LabelDriftEvent(issue=400, add=frozenset({"ns:queued"}), remove=frozenset())
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
