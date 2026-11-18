"""Tests for TestsGate run modes: mapped-first run, durations, interruption (issue #3271)."""
from __future__ import annotations

import json
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

import issuesmith.gates.worktree as wt
from issuesmith.config import reset_config_cache
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






def test_mapped_test_paths_empty(tmp_path: Path) -> None:
    assert _mapped_test_paths(tmp_path, []) == []


# ---------------------------------------------------------------------------
# AC-4: mapped-first run
# ---------------------------------------------------------------------------


def test_mapped_failure_skips_full_run(
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
    assert calls[0] == ["tests/test_foo.py"]






def test_mapped_preexisting_failure_runs_full(
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




def test_mapped_run_logs_elapsed(
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
def test_interrupted_full_run(
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




# ---------------------------------------------------------------------------
# AC-6: failure modes
# ---------------------------------------------------------------------------


def test_changed_files_error_runs_full_once(
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


def test_rc2_stays_collection_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wt, "_run_pytest", lambda root, args: (2, "ERROR collecting\n"))
    monkeypatch.setattr(wt, "changed_files", lambda root, base: [])

    violations = TestsGate(tmp_path, base_branch="main").check("", [])

    assert [v.rule_id for v in violations] == ["tests.collection_error"]


_LAUNCHER = '''\
#!/usr/bin/env python3
import json
import os
import sys

log_path = os.environ["LAUNCHER_LOG"]
with open(log_path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"cwd": os.getcwd(), "role": os.environ.get("LAUNCHER_ROLE", "")}) + "\\n")
os.execv(sys.executable, [sys.executable] + sys.argv[1:])
'''


def _write_launcher(path: Path) -> None:
    path.write_text(_LAUNCHER, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_tests_gate_baseline_and_rerun_use_configured_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ref: sumipan/nexus#7273 AC3 — main, baseline, and flaky reruns share python_command."""
    launcher = tmp_path / "launcher.py"
    _write_launcher(launcher)
    log_path = tmp_path / "launcher-log.jsonl"
    monkeypatch.setenv("LAUNCHER_LOG", str(log_path))

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_command": [str(launcher)], "flaky_reruns": 1},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    clone = _setup_repo(tmp_path, {
        "src/foo.py": _FOO,
        "tests/test_foo.py": _TEST_FOO,
    })
    _commit(clone, {"src/foo.py": "def value():\n    return 2\n"})

    violations = TestsGate(clone, base_branch="main").check("", [])
    assert any(v.rule_id == "tests.pytest_failure" for v in violations)

    entries = [json.loads(ln) for ln in log_path.read_text(encoding="utf-8").splitlines()]
    cwds = {e["cwd"] for e in entries}
    assert str(clone.resolve()) in cwds
    assert any(cwd != str(clone.resolve()) for cwd in cwds)
    assert sum(1 for e in entries if e["cwd"] == str(clone.resolve())) >= 2


def test_tests_gate_fails_when_python_command_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ref: sumipan/nexus#7273 AC6 — missing launcher -> tests.collection_error."""
    missing = tmp_path / "no-such-launcher"
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_command": [str(missing)]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_foo.py").write_text("def test_foo():\n    assert True\n")

    violations = TestsGate(tmp_path, base_branch="main").check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.collection_error"
    assert str(missing) in violations[0].message
