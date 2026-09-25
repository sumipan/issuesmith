"""main_red / main_green evaluate and execute: halt, andon, resume (#3664)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import get_config, reset_config_cache
from issuesmith.observe import observe
from issuesmith.observe.events import MainGreenEvent, MainRedEvent
from issuesmith.observe.main_health import check, state_path
from issuesmith.observe.policy import (
    AndonAction,
    HaltAction,
    ResumeAction,
    evaluate,
    execute,
)
from issuesmith.queue_store import QueueStore

_SHA_A = "a" * 40
_SHA_B = "b" * 40


class RecordingSink:
    def __init__(self) -> None:
        self.emitted: list = []

    def emit(self, andon) -> None:
        self.emitted.append(andon)


class StubClient:
    def issue_get(self, number, fields=None):
        return {"number": number, "state": "OPEN", "labels": []}

    def list_issues(self, label, state="open"):
        return []


def _runner(sha: str, rc: int, stdout: str = ""):
    def run(cmd, **kwargs):
        cmd = list(cmd)
        if cmd[0] == "git":
            out = sha + "\n" if cmd[3] == "rev-parse" else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")
        return subprocess.CompletedProcess(cmd, rc, stdout, "")
    return run


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "repo": "example/repo",
        "observe": {"main_health": {"worktree": str(wt), "command": "python -m pytest -q"}},
    }), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "q.lock",
    )
    yield get_config(), store
    reset_config_cache()


def _tick(config, store: QueueStore, sink: RecordingSink) -> None:
    events = observe(store.snapshot(), StubClient(), config, github_api_low=False)
    execute(evaluate(events, config.observe), store, sinks=[sink])


def test_evaluate_main_red():
    event = MainRedEvent(sha=_SHA_A, reason="exit 1", failing=("t::a", "t::b"))
    actions = evaluate([event], get_config().observe)
    assert actions == [
        HaltAction(
            scope="phase:develop",
            reason=f"main red at {_SHA_A[:12]}: exit 1",
            event_kind="main_red",
            keep_existing=True,
        ),
        AndonAction(
            kind="broken",
            issue=0,
            summary=f"main is red at {_SHA_A[:12]}: exit 1",
            evidence="failing: t::a, t::b",
            key="main_red",
        ),
    ]
    assert actions[1].andon_id == "observe:0:main_red:0"


def test_evaluate_main_green():
    actions = evaluate([MainGreenEvent(sha=_SHA_B)], get_config().observe)
    assert actions == [ResumeAction(reason=f"main green at {_SHA_B[:12]}", event_kind="main_red")]


def test_red_halts_develop_andon_once_then_green_resumes(env):
    config, store = env
    sink = RecordingSink()
    mh = config.observe.main_health
    path = state_path(config)

    red = check(mh, path, run=_runner(_SHA_A, 1, "FAILED tests/test_a.py::test_x\n"))
    assert red.status == "red"
    assert red.failing == ("tests/test_a.py::test_x",)

    _tick(config, store, sink)
    _tick(config, store, sink)

    snap = store.snapshot()
    assert snap.halt is True
    assert snap.halt_scope == "phase:develop"
    assert snap.halt_event == "main_red"
    assert len(sink.emitted) == 1
    assert sink.emitted[0].id == "observe:0:main_red:0"

    green = check(mh, path, run=_runner(_SHA_B, 0))
    assert green.status == "green"

    _tick(config, store, sink)

    assert store.snapshot().halt is False
    assert len(sink.emitted) == 1


def test_red_again_after_green_raises_andon_again(env):
    config, store = env
    sink = RecordingSink()
    mh = config.observe.main_health
    path = state_path(config)

    check(mh, path, run=_runner(_SHA_A, 1))
    _tick(config, store, sink)
    check(mh, path, run=_runner(_SHA_B, 0))
    _tick(config, store, sink)
    check(mh, path, run=_runner("c" * 40, 1))
    _tick(config, store, sink)

    assert len(sink.emitted) == 2
    assert store.snapshot().halt_event == "main_red"


def test_main_red_does_not_override_other_halt(env):
    _, store = env
    store.set_halt(True, "systemic", scope="all", event="systemic_step_failure")

    actions = evaluate([MainRedEvent(sha=_SHA_A, reason="exit 1")], get_config().observe)
    execute(actions, store, sinks=[])

    snap = store.snapshot()
    assert snap.halt is True
    assert snap.halt_scope == "all"
    assert snap.halt_event == "systemic_step_failure"


def test_main_green_does_not_clear_other_halt(env):
    _, store = env
    store.set_halt(True, "systemic", scope="all", event="systemic_step_failure")

    execute(evaluate([MainGreenEvent(sha=_SHA_A)], get_config().observe), store, sinks=[])

    snap = store.snapshot()
    assert snap.halt is True
    assert snap.halt_event == "systemic_step_failure"


def test_resume_without_event_kind_clears_any_halt(env):
    _, store = env
    store.set_halt(True, "systemic", scope="all", event="systemic_step_failure")

    execute([ResumeAction()], store, sinks=[])

    assert store.snapshot().halt is False


def test_halt_without_keep_existing_overwrites(env):
    _, store = env
    store.set_halt(True, "systemic", scope="all", event="systemic_step_failure")

    execute([HaltAction(scope="phase:develop", reason="x", event_kind="forge_unavailable")], store, sinks=[])

    snap = store.snapshot()
    assert (snap.halt_scope, snap.halt_event) == ("phase:develop", "forge_unavailable")
