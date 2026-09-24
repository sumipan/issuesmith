"""Tests for TestsGate baseline comparison (issue #3646)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.gates.worktree import (
    WORKTREE_GATES,
    TestsGate,
    _func_id,
    _parse_failed_ids,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False,
    )


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal origin git repo and a clone. Returns (origin, clone)."""
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    origin.mkdir()

    subprocess.run(
        ["git", "init", "-b", "main", str(origin)],
        capture_output=True, check=False,
    )
    _git(origin, "config", "user.email", "t@t.com")
    _git(origin, "config", "user.name", "T")
    (origin / "conftest.py").write_text("")
    _git(origin, "add", ".")
    _git(origin, "commit", "-m", "init")

    subprocess.run(
        ["git", "clone", str(origin), str(clone)],
        capture_output=True, check=False,
    )
    _git(clone, "config", "user.email", "t@t.com")
    _git(clone, "config", "user.name", "T")

    return origin, clone


def _commit(repo: Path, files: dict[str, str], message: str = "update") -> None:
    """Write files and create a commit in the given repo."""
    for rel, content in files.items():
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


# ---------------------------------------------------------------------------
# AC-1: preexisting failure is non-blocking; only new failure is a Violation
# ---------------------------------------------------------------------------


def test_ac1_preexisting_failure_is_nonblocking(tmp_path: Path) -> None:
    """test_old fails on main (preexisting); test_new fails only on PR -> 1 Violation."""
    origin, clone = _setup_repo(tmp_path)

    # main has test_old that fails
    _commit(origin, {"tests/test_old.py": "def test_old():\n    assert False\n"})
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "origin/main")

    # PR branch adds test_new (also fails, but new)
    _git(clone, "checkout", "-b", "feat")
    _commit(clone, {"tests/test_new.py": "def test_new():\n    assert False\n"})

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "tests.pytest_failure"
    assert "test_new" in v.message
    assert v.fix_hint is not None
    assert "test_old" in v.fix_hint


# ---------------------------------------------------------------------------
# AC-2: collection error (rc=2) triggers fail-safe violation
# ---------------------------------------------------------------------------


def test_ac2_collection_error_is_fail_safe(tmp_path: Path) -> None:
    """Import error in test file (rc=2) -> rule_id='tests.collection_error'."""
    _, clone = _setup_repo(tmp_path)

    _commit(clone, {
        "tests/test_bad_import.py": (
            "import nonexistent_module_xyz_ac2\n"
            "def test_x():\n"
            "    pass\n"
        ),
    })

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.collection_error"


# ---------------------------------------------------------------------------
# AC-3: baseline worktree is cleaned up after check()
# ---------------------------------------------------------------------------


def test_ac3_baseline_worktree_cleaned_up(tmp_path: Path) -> None:
    """After check(), only the clone's main worktree remains in `git worktree list`."""
    origin, clone = _setup_repo(tmp_path)

    _commit(origin, {"tests/test_old.py": "def test_old():\n    assert False\n"})
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "origin/main")
    _git(clone, "checkout", "-b", "feat")
    _commit(clone, {"tests/test_new.py": "def test_new():\n    assert False\n"})

    TestsGate(clone, base_branch="main").check("", [])

    result = subprocess.run(
        ["git", "-C", str(clone), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=False,
    )
    worktree_lines = [ln for ln in result.stdout.splitlines() if ln.startswith("worktree ")]
    assert len(worktree_lines) == 1
    assert str(clone) in worktree_lines[0]


# ---------------------------------------------------------------------------
# AC-5: all pass -> []; only preexisting -> []
# ---------------------------------------------------------------------------


def test_ac5_all_pass_returns_empty(tmp_path: Path) -> None:
    """All tests pass -> []."""
    _, clone = _setup_repo(tmp_path)

    _commit(clone, {"tests/test_pass.py": "def test_pass():\n    assert True\n"})

    assert TestsGate(clone, base_branch="main").check("", []) == []


def test_ac5_only_preexisting_failures_returns_empty(tmp_path: Path) -> None:
    """All failures are preexisting on main -> []."""
    origin, clone = _setup_repo(tmp_path)

    _commit(origin, {"tests/test_old.py": "def test_old():\n    assert False\n"})
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "origin/main")
    _git(clone, "checkout", "-b", "feat")

    assert TestsGate(clone, base_branch="main").check("", []) == []


# ---------------------------------------------------------------------------
# AC-6: two independent new failures give two Violations (no -x)
# ---------------------------------------------------------------------------


def test_ac6_two_new_failures_give_two_violations(tmp_path: Path) -> None:
    """Two independent new failures -> 2 Violations (gate runs without -x)."""
    _, clone = _setup_repo(tmp_path)

    _commit(clone, {
        "tests/test_fail_a.py": "def test_fail_a():\n    assert False\n",
        "tests/test_fail_b.py": "def test_fail_b():\n    assert False\n",
    })

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert len(violations) == 2
    assert all(v.rule_id == "tests.pytest_failure" for v in violations)


# ---------------------------------------------------------------------------
# AC-7: no origin remote -> all failures are new with "baseline unavailable"
# ---------------------------------------------------------------------------


def test_ac7_no_origin_remote_baseline_unavailable(tmp_path: Path) -> None:
    """No origin remote -> all failures get rule_id=pytest_failure and 'baseline unavailable'."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        capture_output=True, check=False,
    )
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "conftest.py").write_text("")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")

    _commit(repo, {"tests/test_fail.py": "def test_fail():\n    assert False\n"})

    violations = TestsGate(repo, base_branch="main").check("", [])

    assert len(violations) >= 1
    assert all(v.rule_id == "tests.pytest_failure" for v in violations)
    assert any("baseline unavailable" in v.message for v in violations)


# ---------------------------------------------------------------------------
# AC-8: space in parametrize ID is treated as one ID; preexisting -> []
# ---------------------------------------------------------------------------


def test_ac8_parametrized_id_with_space_is_preexisting(tmp_path: Path) -> None:
    """Parametrized ID 'test_param[a b]' that fails on main is preexisting -> []."""
    origin, clone = _setup_repo(tmp_path)

    test_code = (
        "import pytest\n"
        "@pytest.mark.parametrize('v', ['a b'])\n"
        "def test_param(v):\n"
        "    assert False\n"
    )
    _commit(origin, {"tests/test_param.py": test_code})
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "origin/main")
    _git(clone, "checkout", "-b", "feat")

    assert TestsGate(clone, base_branch="main").check("", []) == []


def test_parse_failed_ids_space_in_param_is_single_id() -> None:
    """_parse_failed_ids does not split on spaces inside parameter brackets."""
    output = "FAILED tests/test_p.py::test_func[a b] - AssertionError: assert False"
    ids = _parse_failed_ids(output)
    assert ids == ["tests/test_p.py::test_func[a b]"]


# ---------------------------------------------------------------------------
# AC-9: WORKTREE_GATES["tests"] factory passes base_branch
# ---------------------------------------------------------------------------


def test_ac9_worktree_gates_tests_passes_base_branch(tmp_path: Path) -> None:
    """WORKTREE_GATES['tests'](path, [], 'develop') returns TestsGate with _base='develop'."""
    gate = WORKTREE_GATES["tests"](tmp_path, [], "develop")
    assert isinstance(gate, TestsGate)
    assert gate._base == "develop"


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


def test_func_id_strips_parameters() -> None:
    assert _func_id("tests/t.py::test_f[a b]") == "tests/t.py::test_f"
    assert _func_id("tests/t.py::test_f[1]") == "tests/t.py::test_f"
    assert _func_id("tests/t.py::test_f") == "tests/t.py::test_f"


def test_parse_failed_ids_deduplicates() -> None:
    output = (
        "FAILED tests/t.py::test_a - err\n"
        "FAILED tests/t.py::test_a - err\n"
        "ERROR tests/t.py::test_b\n"
    )
    ids = _parse_failed_ids(output)
    assert ids == ["tests/t.py::test_a", "tests/t.py::test_b"]


def test_parse_failed_ids_ignores_non_failed_lines() -> None:
    output = "PASSED tests/t.py::test_x\nfailed (exit code 1)\n"
    assert _parse_failed_ids(output) == []
