"""tests/observe/test_policy.py -- policy evaluation and execution tests (AC-2, AC-5, AC-7)."""
from __future__ import annotations

from pathlib import Path

from ghdag.forge import get_forge

from issuesmith.andon import list_open
from issuesmith.config import ObserveConfig
from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    DagTerminatedEvent,
    IssueStallEvent,
    LabelDriftEvent,
    OrphanExecEvent,
    SystemicStepFailureEvent,
)
from issuesmith.observe.policy import (
    AndonAction,
    HaltAction,
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


def test_dag_terminated_andon_facts_include_failure_details():
    """Refs #8076 AC8."""
    event = DagTerminatedEvent(
        issue=12,
        key="issuesmith:impl:12",
        failed_step="p1",
        failed_uuid="u1",
        failure_class="TIMEOUT",
        stderr_tail="tail",
        namespace="issuesmith",
        result_path="/jobs/done/u1",
    )
    actions = evaluate([event], ObserveConfig())
    andon = next(a for a in actions if isinstance(a, AndonAction))
    assert andon.facts == {
        "failed_step": "p1",
        "trigger": "dag_terminated",
        "failure_class": "TIMEOUT",
        "stderr_tail": "tail",
    }
    assert andon.summary.startswith("DAG ")
    assert andon.evidence == "uuid=u1 result=/jobs/done/u1"


def test_dag_terminated_andon_omits_empty_failure_facts(tmp_path):
    """Refs #8076 AC8 empty failure_class and stderr_tail."""
    event = DagTerminatedEvent(
        issue=13,
        key="issuesmith:impl:13",
        failed_step="p1",
        failed_uuid="u1",
        namespace="issuesmith",
    )
    andon = next(a for a in evaluate([event], ObserveConfig()) if isinstance(a, AndonAction))
    assert andon.facts == {"failed_step": "p1", "trigger": "dag_terminated"}


def test_dag_terminated_execute_passes_facts_to_andon(tmp_path, monkeypatch):
    """Refs #8076 AC8 facts reach raised andon."""
    import yaml

    from issuesmith.config import reset_config_cache

    forge_root = tmp_path / "forge"
    forge_root.mkdir()
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(forge_root))
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_REPOSITORIES", "GITHUB_REPOSITORY"):
        monkeypatch.delenv(key, raising=False)
    reset_config_cache()
    client = get_forge()
    store = _store(tmp_path)
    issue = client.issue_create("andon facts", "body")
    event = DagTerminatedEvent(
        issue=issue,
        key=f"issuesmith:impl:{issue}",
        failed_step="p1",
        failed_uuid="u1",
        failure_class="TIMEOUT",
        stderr_tail="tail",
        namespace="issuesmith",
        result_path="/jobs/done/u1",
    )
    execute(evaluate([event], ObserveConfig()), store, sinks=[], client=client)
    open_andons = list_open(client)
    assert len(open_andons) == 1
    assert open_andons[0].facts == {
        "failed_step": "p1",
        "trigger": "dag_terminated",
        "failure_class": "TIMEOUT",
        "stderr_tail": "tail",
    }
    reset_config_cache()
