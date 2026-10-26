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
        self.fail_git_at: str | None = None
        self.fail_git_op: str | None = None
        self.timeout = False
        self.calls: list[list[str]] = []
        self.command_calls: list[dict] = []

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[0] == "git":
            git_cwd = cmd[2] if len(cmd) > 2 else ""
            sub = cmd[3]
            git_op = cmd[4] if len(cmd) > 4 else ""
            if sub == self.fail_git and (
                self.fail_git_at is None or self.fail_git_at == git_cwd
            ) and (self.fail_git_op is None or self.fail_git_op == git_op):
                return subprocess.CompletedProcess(cmd, 128, "", "fatal: boom")
            if sub == "worktree" and len(cmd) > 4 and cmd[4] == "add":
                Path(cmd[6]).mkdir(parents=True, exist_ok=True)
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


def test_check_creates_worktree_then_runs_health(mh_cfg, tmp_path):
    repo_root = tmp_path / "clone"
    repo_root.mkdir()
    worktree = tmp_path / "wt" / "main-health"
    cfg = dataclasses.replace(mh_cfg, worktree=worktree)
    path = tmp_path / "mh.json"
    run = FakeRunner(rc=0)

    state = check(cfg, path, repo_root=repo_root, run=run)

    assert state.status == "green"
    assert worktree.is_dir()
    prune_idx = next(
        i for i, c in enumerate(run.calls) if c[3:] == ["worktree", "prune"]
    )
    fetch_repo_idx = next(
        i
        for i, c in enumerate(run.calls)
        if c[2] == str(repo_root) and c[3:] == ["fetch", "origin", "main"]
    )
    add_idx = next(
        i
        for i, c in enumerate(run.calls)
        if c[3:] == ["worktree", "add", "--detach", str(worktree), "FETCH_HEAD"]
    )
    fetch_wt_idx = next(
        i
        for i, c in enumerate(run.calls)
        if c[2] == str(worktree) and c[3:] == ["fetch", "origin", "main"]
    )
    assert prune_idx < fetch_repo_idx < add_idx < fetch_wt_idx
    assert run.command_calls


def test_check_worktree_path_is_file_raises(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    cfg = dataclasses.replace(mh_cfg, worktree=blocker)
    repo_root = tmp_path / "clone"
    repo_root.mkdir()
    run = FakeRunner()
    with pytest.raises(MainHealthError, match="not a directory"):
        check(cfg, path, repo_root=repo_root, run=run)
    assert run.calls == []
    assert run.command_calls == []


@pytest.mark.parametrize("fail_sub", ["prune", "fetch", "add"])
def test_check_worktree_create_git_failure_keeps_state(mh_cfg, tmp_path, fail_sub):
    path = tmp_path / "mh.json"
    check(mh_cfg, path, run=FakeRunner(rc=1))
    before = path.read_bytes()

    repo_root = tmp_path / "clone"
    repo_root.mkdir()
    worktree = tmp_path / "new-wt"
    cfg = dataclasses.replace(mh_cfg, worktree=worktree)
    run = FakeRunner(sha=_SHA_B)
    if fail_sub == "prune":
        run.fail_git = "worktree"
        run.fail_git_at = str(repo_root)
        run.fail_git_op = "prune"
    elif fail_sub == "add":
        run.fail_git = "worktree"
        run.fail_git_at = str(repo_root)
        run.fail_git_op = "add"
    else:
        run.fail_git = "fetch"
        run.fail_git_at = str(repo_root)

    with pytest.raises(MainHealthError, match="fatal: boom"):
        check(cfg, path, repo_root=repo_root, run=run)

    assert path.read_bytes() == before
    assert run.command_calls == []


def test_check_existing_worktree_skips_create_commands(mh_cfg, tmp_path):
    path = tmp_path / "mh.json"
    repo_root = tmp_path / "clone"
    repo_root.mkdir()
    run = FakeRunner(rc=0)
    check(mh_cfg, path, repo_root=repo_root, run=run)

    create_cmds = [
        c for c in run.calls if c[3:] == ["worktree", "prune"] or (
            len(c) > 4 and c[3] == "worktree" and c[4] == "add"
        )
    ]
    repo_fetches = [c for c in run.calls if c[2] == str(repo_root)]
    assert create_cmds == []
    assert repo_fetches == []


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
    monkeypatch.setattr(
        "issuesmith.observe.main_health.check",
        lambda cfg, path, repo_root=None: state,
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-health"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert f"main_health: red {_SHA_A[:12]}" in out
    assert "tests/test_a.py::test_x" in out


def test_cli_error_exits_two(enabled, monkeypatch, capsys):
    def boom(cfg, path, repo_root=None):
        raise MainHealthError("git fetch failed")

    monkeypatch.setattr("issuesmith.observe.main_health.check", boom)
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-health"])
    assert exc.value.code == 2
    assert "git fetch failed" in capsys.readouterr().err


def test_cli_prints_created_worktree_line(enabled, monkeypatch, capsys, tmp_path):
    state = MainHealthState(
        sha=_SHA_A, status="green", reason="", failing=(), checked_at="",
    )
    wt = enabled.observe.main_health.worktree

    def fake_check(cfg, path, repo_root=None):
        cfg.worktree.mkdir(parents=True, exist_ok=True)
        return state

    monkeypatch.setattr("issuesmith.observe.main_health.check", fake_check)
    enabled.observe.main_health.worktree.rmdir()
    with pytest.raises(SystemExit) as exc:
        cli.main(["main-health"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert f"main_health: created worktree {wt}" in out
    assert f"main_health: green {_SHA_A[:12]}" in out
    assert out.index("created worktree") < out.index("green")


def test_check_integration_creates_worktree_and_green(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    subprocess.run(["git", "-C", str(seed), "checkout", "-q", "-b", "main"], check=True)
    subprocess.run(
        ["git", "-C", str(seed), "commit", "--allow-empty", "-q", "-m", "init"],
        check=True,
        env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"},
    )
    subprocess.run(
        ["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", str(origin)],
        check=True,
    )
    worktree = tmp_path / "health-wt"
    cfg = MainHealthConfig(
        worktree=worktree,
        command=("true",),
        base_branch="main",
        timeout_seconds=60,
    )
    path = tmp_path / "state" / "issuesmith-main-health.json"
    state = check(cfg, path, repo_root=clone)

    assert worktree.is_dir()
    assert state.status == "green"
    assert load_state(path) == state
