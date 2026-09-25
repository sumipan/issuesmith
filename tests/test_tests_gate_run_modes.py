"""Tests for TestsGate run modes: mapped-first run, durations, interruption (issue #3271)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import issuesmith.gates.worktree as wt
from issuesmith.gates.worktree import TestsGate, _mapped_test_paths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False,
    )


def _commit(repo: Path, files: dict[str, str], message: str = "update") -> None:
    for rel, content in files.items():
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _setup_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create origin with `files` on main and return a clone on a feature branch."""
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(origin)], capture_output=True, check=False)
    _git(origin, "config", "user.email", "t@t.com")
    _git(origin, "config", "user.name", "T")
    _commit(origin, {"conftest.py": "", **files}, "init")
    subprocess.run(["git", "clone", str(origin), str(clone)], capture_output=True, check=False)
    _git(clone, "config", "user.email", "t@t.com")
    _git(clone, "config", "user.name", "T")
    _git(clone, "checkout", "-b", "feat")
    return clone


def _count_calls(monkeypatch: pytest.MonkeyPatch, root: Path) -> list[list[str]]:
    """Wrap _run_pytest and record the args of calls made in `root` (not the baseline)."""
    calls: list[list[str]] = []
    real = wt._run_pytest

    def _spy(r: Path, args: list[str]) -> tuple[int, str]:
        if Path(r) == root:
            calls.append(list(args))
        return real(r, args)

    monkeypatch.setattr(wt, "_run_pytest", _spy)
    return calls


_FOO = "def value():\n    return 1\n"
_TEST_FOO = "from foo import value\n\n\ndef test_value():\n    assert value() == 1\n"
_TEST_OTHER = "def test_other():\n    assert True\n"


# ---------------------------------------------------------------------------
# _mapped_test_paths
# ---------------------------------------------------------------------------


def test_mapped_test_paths_maps_sources_only(tmp_path: Path) -> None:
    for rel in ("tests/test_foo.py", "tests/sub/test_foo_extra.py", "tests/test_bar.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("")
    changed = [
        "src/foo.py",
        "tests/test_bar.py",
        "tests/test_gone.py",
        "src/nomatch.py",
        "README.md",
        "tests/conftest.py",
    ]
    assert _mapped_test_paths(tmp_path, changed) == [
        "tests/sub/test_foo_extra.py",
        "tests/test_foo.py",
    ]


def test_mapped_test_paths_changed_test_only_runs_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed test file with no changed source is left to the full run."""
    clone = _setup_repo(tmp_path, {"tests/test_other.py": _TEST_OTHER})
    _commit(clone, {"tests/test_new.py": "def test_new():\n    assert False\n"})
    calls = _count_calls(monkeypatch, clone)

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert [v.rule_id for v in violations] == ["tests.pytest_failure"]
    assert len(calls) == 1
    assert "--durations=20" in calls[0]


def test_mapped_test_paths_empty(tmp_path: Path) -> None:
    assert _mapped_test_paths(tmp_path, []) == []


# ---------------------------------------------------------------------------
# AC-4: mapped-first run
# ---------------------------------------------------------------------------


def test_ac4_mapped_failure_skips_full_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _setup_repo(tmp_path, {
        "src/foo.py": _FOO,
        "tests/test_foo.py": _TEST_FOO,
        "tests/test_other.py": _TEST_OTHER,
    })
    _commit(clone, {"src/foo.py": "def value():\n    return 2\n"})
    calls = _count_calls(monkeypatch, clone)

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert [v.rule_id for v in violations] == ["tests.pytest_failure"]
    assert "tests/test_foo.py::test_value" in violations[0].message
    assert len(calls) == 1
    assert calls[0] == ["tests/test_foo.py", "-x"]


def test_ac4_mapped_pass_runs_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = _setup_repo(tmp_path, {
        "src/foo.py": _FOO,
        "tests/test_foo.py": _TEST_FOO,
        "tests/test_other.py": _TEST_OTHER,
    })
    _commit(clone, {"src/foo.py": _FOO + "\n\ndef extra():\n    return 3\n"})
    calls = _count_calls(monkeypatch, clone)

    assert TestsGate(clone, base_branch="main").check("", []) == []
    assert len(calls) == 2
    assert calls[0] == ["tests/test_foo.py", "-x"]
    assert "--durations=20" in calls[1]


def test_ac4_no_mapping_runs_full_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _setup_repo(tmp_path, {"tests/test_other.py": _TEST_OTHER})
    _commit(clone, {"src/bar.py": "X = 1\n"})
    calls = _count_calls(monkeypatch, clone)

    assert TestsGate(clone, base_branch="main").check("", []) == []
    assert len(calls) == 1
    assert "--durations=20" in calls[0]


def test_ac4_mapped_preexisting_failure_runs_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mapped failure that also fails on base is non-blocking; the full run decides."""
    clone = _setup_repo(tmp_path, {
        "src/foo.py": _FOO,
        "tests/test_foo.py": "def test_value():\n    assert False\n",
        "tests/test_other.py": _TEST_OTHER,
    })
    _commit(clone, {"src/foo.py": _FOO + "\n\ndef extra():\n    return 3\n"})
    calls = _count_calls(monkeypatch, clone)

    assert TestsGate(clone, base_branch="main").check("", []) == []
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# AC-2: durations and timing on stderr
# ---------------------------------------------------------------------------

_FULL_OUTPUT = (
    "..                                                                       [100%]\n"
    "============================= slowest 20 durations =============================\n"
    "1.11s call     tests/test_x.py::test_a\n"
    "\n"
    "(5 durations < 1s hidden.)\n"
    "2 passed in 1.12s\n"
)


def test_ac2_full_run_durations_and_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def _fake(root: Path, args: list[str]) -> tuple[int, str]:
        calls.append(list(args))
        return 0, _FULL_OUTPUT

    monkeypatch.setattr(wt, "_run_pytest", _fake)
    monkeypatch.setattr(wt, "changed_files", lambda root, base: [])

    assert TestsGate(tmp_path, base_branch="main").check("", []) == []

    assert calls == [[str(tmp_path / "tests"), "--durations=20", "--durations-min=1.0"]]
    err = capsys.readouterr().err
    assert "[tests] full: rc=0 elapsed=" in err
    assert "slowest 20 durations" in err
    assert "1.11s call     tests/test_x.py::test_a" in err
    assert "2 passed in 1.12s" in err


def test_ac2_mapped_run_logs_elapsed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    monkeypatch.setattr(wt, "_run_pytest", lambda root, args: (0, "1 passed in 0.01s\n"))
    monkeypatch.setattr(wt, "changed_files", lambda root, base: ["src/foo.py"])

    assert TestsGate(tmp_path, base_branch="main").check("", []) == []

    err = capsys.readouterr().err
    assert "[tests] mapped: rc=0 elapsed=" in err
    assert "[tests] full: rc=0 elapsed=" in err


# ---------------------------------------------------------------------------
# AC-5: interruption by external signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rc", [143, 137, -15, -9])
def test_ac5_interrupted_full_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int
) -> None:
    monkeypatch.setattr(wt, "_run_pytest", lambda root, args: (rc, "Terminated\n"))
    monkeypatch.setattr(wt, "changed_files", lambda root, base: [])

    violations = TestsGate(tmp_path, base_branch="main").check("", [])

    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "tests.interrupted"
    assert f"rc={rc}" in v.message
    assert v.auto_fixable is False
    assert v.fix_hint == "Rerun only; do not modify code"


@pytest.mark.parametrize("rc", [143, -9])
def test_ac5_interrupted_mapped_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rc: int
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    calls: list[list[str]] = []

    def _fake(root: Path, args: list[str]) -> tuple[int, str]:
        calls.append(list(args))
        return rc, ""

    monkeypatch.setattr(wt, "_run_pytest", _fake)
    monkeypatch.setattr(wt, "changed_files", lambda root, base: ["src/foo.py"])

    violations = TestsGate(tmp_path, base_branch="main").check("", [])

    assert [v.rule_id for v in violations] == ["tests.interrupted"]
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# AC-6: failure modes
# ---------------------------------------------------------------------------


def test_ac6_changed_files_error_runs_full_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("")
    calls: list[list[str]] = []

    def _fake(root: Path, args: list[str]) -> tuple[int, str]:
        calls.append(list(args))
        return 0, "1 passed in 0.01s\n"

    def _boom(root: Path, base: str) -> list[str]:
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(wt, "_run_pytest", _fake)
    monkeypatch.setattr(wt, "changed_files", _boom)

    assert TestsGate(tmp_path, base_branch="main").check("", []) == []
    assert len(calls) == 1
    assert "--durations=20" in calls[0]


def test_ac6_rc2_stays_collection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wt, "_run_pytest", lambda root, args: (2, "ERROR collecting\n"))
    monkeypatch.setattr(wt, "changed_files", lambda root, base: [])

    violations = TestsGate(tmp_path, base_branch="main").check("", [])

    assert [v.rule_id for v in violations] == ["tests.collection_error"]


def test_mapped_run_no_tests_collected_runs_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rc=5 (nothing collected, e.g. a helper-only mapped file) is not an error; run full."""
    clone = _setup_repo(tmp_path, {
        "src/foo.py": _FOO,
        "tests/test_foo_data.py": "DATA = 1\n",
        "tests/test_other.py": _TEST_OTHER,
    })
    _commit(clone, {"src/foo.py": _FOO + "\n\ndef extra():\n    return 3\n"})
    calls = _count_calls(monkeypatch, clone)

    assert TestsGate(clone, base_branch="main").check("", []) == []
    assert len(calls) == 2
    assert calls[0] == ["tests/test_foo_data.py", "-x"]
    assert "--durations=20" in calls[1]
