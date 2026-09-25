"""observe.main_health: config, check(), load_state(), detector and CLI (#3664)."""
from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith import cli
from issuesmith.config import MainHealthConfig, get_config, reset_config_cache
from issuesmith.observe import _detect_main_health, observe
from issuesmith.observe.events import MainGreenEvent, MainRedEvent
from issuesmith.observe.main_health import (
    MainHealthError,
    MainHealthState,
    check,
    load_state,
    state_path,
)
from issuesmith.queue_store import QueueStore

_SHA_A = "a" * 40
_SHA_B = "b" * 40


class FakeRunner:
    """Stands in for subprocess.run: answers git calls and records command runs."""

    def __init__(self, sha: str = _SHA_A, rc: int = 0, stdout: str = "") -> None:
        self.sha = sha
        self.rc = rc
        self.stdout = stdout
        self.fail_git: str | None = None
        self.timeout = False
        self.calls: list[list[str]] = []
        self.command_calls: list[dict] = []

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[0] == "git":
            sub = cmd[3]
            if sub == self.fail_git:
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: boom")
            out = self.sha + "\n" if sub == "rev-parse" else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")
        self.command_calls.append(kwargs)
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return subprocess.CompletedProcess(cmd, self.rc, self.stdout, "")


class StubClient:
    def issue_get(self, number, fields=None):
        return {"number": number, "state": "OPEN", "labels": []}

    def list_issues(self, label, state="open"):
        return []


def _write_cfg(tmp_path: Path, monkeypatch, observe_raw: dict | None) -> None:
    data: dict = {"repo": "example/repo"}
    if observe_raw is not None:
        data["observe"] = observe_raw
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


@pytest.fixture
def mh_cfg(tmp_path: Path) -> MainHealthConfig:
    wt = tmp_path / "wt"
    wt.mkdir()
    return MainHealthConfig(worktree=wt, command=("python", "-m", "pytest", "-q"))


@pytest.fixture
def enabled(tmp_path: Path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    _write_cfg(tmp_path, monkeypatch, {
        "main_health": {"worktree": str(wt), "command": "python -m pytest -q"},
    })
    yield get_config()
    reset_config_cache()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "q.lock",
    )


# --- config ---------------------------------------------------------------


def test_config_absent_is_disabled(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {"stall_minutes": 10})
    assert get_config().observe.main_health is None


def test_config_parses_str_and_defaults(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {
        "main_health": {"worktree": "/var/tmp/mh", "command": "python -m pytest -q -x tests/"},
    })
    mh = get_config().observe.main_health
    assert mh == MainHealthConfig(
        worktree=Path("/var/tmp/mh"),
        command=("python", "-m", "pytest", "-q", "-x", "tests/"),
        base_branch="main",
        timeout_seconds=1800,
    )


def test_config_parses_list_and_overrides(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {
        "main_health": {
            "worktree": "/var/tmp/mh",
            "command": ["make", "test"],
            "base_branch": "develop",
            "timeout_seconds": 60,
        },
    })
    mh = get_config().observe.main_health
    assert mh.command == ("make", "test")
    assert mh.base_branch == "develop"
    assert mh.timeout_seconds == 60


@pytest.mark.parametrize("raw", [
    {"worktree": "", "command": "pytest"},
    {"command": "pytest"},
    {"worktree": "/var/tmp/mh", "command": ""},
    {"worktree": "/var/tmp/mh", "command": []},
    {"worktree": "/var/tmp/mh"},
])
def test_config_empty_worktree_or_command_raises(tmp_path, monkeypatch, raw):
    _write_cfg(tmp_path, monkeypatch, {"main_health": raw})
    with pytest.raises(ValueError):
        get_config()


def test_state_path_is_next_to_queue_state(enabled):
    assert state_path(enabled) == enabled.paths.queue_state.parent / "issuesmith-main-health.json"


# --- check() ----------------------------------------------------------------


def test_check_red_writes_state_with_failing_ids(mh_cfg, tmp_path):
    path = tmp_path / "logs" / "mh.json"
    run = FakeRunner(rc=1, stdout="FAILED tests/test_a.py::test_x - assert 0\n1 failed\n")

    state = check(mh_cfg, path, run=run)

    assert state.status == "red"
    assert state.sha == _SHA_A
    assert state.reason == "exit 1"
    assert state.failing == ("tests/test_a.py::test_x",)
    assert load_state(path) == state
    assert ["git", "-C", str(mh_cfg.worktree), "fetch", "origin", "main"] in run.calls
    assert ["git", "-C", str(mh_cfg.worktree), "checkout", "--detach", "--force", _SHA_A] in run.calls
    assert run.command_calls[0]["cwd"] == str(mh_cfg.worktree)
    assert run.command_calls[0]["timeout"] == 1800


def test_check_green(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    state = check(mh_cfg, path, run=FakeRunner(rc=0))
    assert (state.status, state.reason, state.failing) == ("green", "", ())
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "green"


def test_check_same_sha_skips_command(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    first = check(mh_cfg, path, run=FakeRunner(rc=1))

    run = FakeRunner(rc=0)
    again = check(mh_cfg, path, run=run)

    assert run.command_calls == []
    assert again == first


def test_check_new_sha_runs_command_once(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    check(mh_cfg, path, run=FakeRunner(sha=_SHA_A, rc=1))

    run = FakeRunner(sha=_SHA_B, rc=0)
    state = check(mh_cfg, path, run=run)

    assert len(run.command_calls) == 1
    assert (state.sha, state.status) == (_SHA_B, "green")


@pytest.mark.parametrize("fail_git", ["fetch", "rev-parse", "checkout"])
def test_check_git_failure_keeps_state(mh_cfg, tmp_path, fail_git):
    path = tmp_path / "mh.json"
    check(mh_cfg, path, run=FakeRunner(sha=_SHA_A, rc=1))
    before = path.read_bytes()

    run = FakeRunner(sha=_SHA_B, rc=0)
    run.fail_git = fail_git
    with pytest.raises(MainHealthError):
        check(mh_cfg, path, run=run)

    assert path.read_bytes() == before
    assert run.command_calls == []


def test_check_missing_worktree_keeps_state(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    check(mh_cfg, path, run=FakeRunner(rc=1))
    before = path.read_bytes()

    cfg = dataclasses.replace(mh_cfg, worktree=tmp_path / "missing")
    run = FakeRunner(sha=_SHA_B)
    with pytest.raises(MainHealthError):
        check(cfg, path, run=run)

    assert path.read_bytes() == before
    assert run.calls == []


def test_check_timeout_keeps_state(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    check(mh_cfg, path, run=FakeRunner(rc=0))
    before = path.read_bytes()

    run = FakeRunner(sha=_SHA_B)
    run.timeout = True
    with pytest.raises(MainHealthError):
        check(mh_cfg, path, run=run)

    assert path.read_bytes() == before
    assert list(tmp_path.glob("*.tmp")) == []


def test_check_timeout_without_prior_state_writes_nothing(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    run = FakeRunner()
    run.timeout = True
    with pytest.raises(MainHealthError):
        check(mh_cfg, path, run=run)
    assert not path.exists()


# --- load_state() -------------------------------------------------------------


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    json.dumps({"sha": _SHA_A, "status": "yellow", "reason": "", "failing": [], "checked_at": ""}),
    json.dumps({"sha": 1, "status": "red", "reason": "", "failing": [], "checked_at": ""}),
    json.dumps({"sha": _SHA_A, "status": "red", "reason": "", "failing": "x", "checked_at": ""}),
])
def test_load_state_invalid_returns_none(tmp_path, content):
    path = tmp_path / "mh.json"
    path.write_text(content, encoding="utf-8")
    assert load_state(path) is None


def test_load_state_missing_returns_none(tmp_path):
    assert load_state(tmp_path / "absent.json") is None


# --- detector -------------------------------------------------------------------


def _put_state(config, status: str, sha: str = _SHA_A) -> None:
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "sha": sha,
        "status": status,
        "reason": "exit 1" if status == "red" else "",
        "failing": ["tests/test_a.py::test_x"] if status == "red" else [],
        "checked_at": "2026-09-26T00:00:00+00:00",
    }), encoding="utf-8")


def test_detector_disabled_returns_empty(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, None)
    config = get_config()
    _put_state(config, "red")
    assert _detect_main_health(_store(tmp_path).snapshot(), config) == []


def test_detector_no_state_returns_empty(enabled, tmp_path):
    assert _detect_main_health(_store(tmp_path).snapshot(), enabled) == []


def test_detector_corrupt_state_returns_empty(enabled, tmp_path):
    path = state_path(enabled)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    assert _detect_main_health(_store(tmp_path).snapshot(), enabled) == []


def test_detector_red_emits_main_red(enabled, tmp_path):
    _put_state(enabled, "red")
    assert _detect_main_health(_store(tmp_path).snapshot(), enabled) == [
        MainRedEvent(sha=_SHA_A, reason="exit 1", failing=("tests/test_a.py::test_x",)),
    ]


def test_detector_green_while_main_red_halt_emits_green(enabled, tmp_path):
    _put_state(enabled, "green")
    store = _store(tmp_path)
    store.set_halt(True, "main red", scope="phase:develop", event="main_red")
    assert _detect_main_health(store.snapshot(), enabled) == [MainGreenEvent(sha=_SHA_A)]


def test_detector_green_without_main_red_halt_is_quiet(enabled, tmp_path):
    _put_state(enabled, "green")
    store = _store(tmp_path)
    assert _detect_main_health(store.snapshot(), enabled) == []
    store.set_halt(True, "manual", scope="all", event="systemic_step_failure")
    assert _detect_main_health(store.snapshot(), enabled) == []


class _NoApiClient:
    def __getattr__(self, name):
        raise AssertionError(f"forge API called in reduced mode: {name}")


def test_observe_includes_main_health_in_both_modes(enabled, tmp_path):
    _put_state(enabled, "red")
    snapshot = _store(tmp_path).snapshot()

    normal = observe(snapshot, StubClient(), enabled, github_api_low=False)
    reduced = observe(snapshot, _NoApiClient(), enabled, github_api_low=True)

    assert any(isinstance(e, MainRedEvent) for e in normal)
    assert any(isinstance(e, MainRedEvent) for e in reduced)


# --- CLI ------------------------------------------------------------------------


def test_cli_disabled(tmp_path, monkeypatch, capsys):
    _write_cfg(tmp_path, monkeypatch, None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-health"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "main_health: disabled"


def test_cli_red_prints_failing_and_exits_zero(enabled, monkeypatch, capsys):
    state = MainHealthState(
        sha=_SHA_A, status="red", reason="exit 1",
        failing=("tests/test_a.py::test_x",), checked_at="",
    )
    monkeypatch.setattr("issuesmith.observe.main_health.check", lambda cfg, path: state)
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-health"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert f"main_health: red {_SHA_A[:12]}" in out
    assert "tests/test_a.py::test_x" in out


def test_cli_error_exits_two(enabled, monkeypatch, capsys):
    def boom(cfg, path):
        raise MainHealthError("git fetch failed")

    monkeypatch.setattr("issuesmith.observe.main_health.check", boom)
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-health"])
    assert exc.value.code == 2
    assert "git fetch failed" in capsys.readouterr().err
