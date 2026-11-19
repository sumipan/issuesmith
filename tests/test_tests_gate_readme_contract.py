"""Tests gate treats ``readme_contract`` marker failures as warnings (issue #7415)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import load_config, reset_config_cache
from issuesmith.gates.worktree import TestsGate

_MINIMAL_PHASES = [
    {"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"},
]

_PYTEST_MARKERS = """\
[tool.pytest.ini_options]
markers = ["readme_contract: README alignment (non-blocking in P1)"]
"""


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _commit(repo: Path, files: dict[str, str], message: str = "update") -> None:
    for rel, content in files.items():
        fp = repo / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _setup_repo(tmp_path: Path, files: dict[str, str]) -> Path:
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


def _write_host_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tests: dict[str, object]
) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "phases": _MINIMAL_PHASES,
            "tests": {"flaky_reruns": 0, **tests},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _marked_fail_test() -> str:
    return (
        "import pytest\n\n"
        "@pytest.mark.readme_contract\n"
        "def test_doc():\n"
        "    assert False\n"
    )


def _plain_fail_test() -> str:
    return "def test_plain():\n    assert False\n"


def _passing_src() -> str:
    return "def value():\n    return 1\n"


def test_marked_failure_only_passes_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7415 AC1 — marked readme failure is warn-only when README.md is out of scope."""
    _write_host_config(tmp_path, monkeypatch, {})
    clone = _setup_repo(tmp_path, {
        "pyproject.toml": _PYTEST_MARKERS,
        "src/foo.py": _passing_src(),
        "tests/test_readme.py": (
            "import pytest\n\n"
            "@pytest.mark.readme_contract\n"
            "def test_doc():\n"
            "    assert True\n"
        ),
    })
    _commit(clone, {"tests/test_readme.py": _marked_fail_test(), "src/foo.py": "x = 1\n"})

    violations = TestsGate(clone, allow_paths=["src/**"], base_branch="main").check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.readme_contract"
    assert violations[0].severity == "warn"
    assert not any(v.severity == "fail" for v in violations)


def test_unmarked_failure_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7415 AC2 — unmarked new failure stays blocking."""
    _write_host_config(tmp_path, monkeypatch, {})
    clone = _setup_repo(tmp_path, {
        "pyproject.toml": _PYTEST_MARKERS,
        "tests/test_plain.py": "def test_plain():\n    assert True\n",
    })
    _commit(clone, {"tests/test_plain.py": _plain_fail_test()})

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.pytest_failure"
    assert violations[0].severity == "fail"


def test_readme_in_allow_paths_blocks_marked_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7415 AC3 — README.md in allow_paths keeps marked failures blocking."""
    _write_host_config(tmp_path, monkeypatch, {})
    clone = _setup_repo(tmp_path, {
        "pyproject.toml": _PYTEST_MARKERS,
        "tests/test_readme.py": (
            "import pytest\n\n"
            "@pytest.mark.readme_contract\n"
            "def test_doc():\n"
            "    assert True\n"
        ),
    })
    _commit(clone, {"tests/test_readme.py": _marked_fail_test()})

    violations = TestsGate(
        clone, allow_paths=["README.md"], base_branch="main"
    ).check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.pytest_failure"
    assert violations[0].severity == "fail"


def test_strict_markers_without_registration_still_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7415 AC4 — collect rc=4 must not hide unmarked failures."""
    _write_host_config(tmp_path, monkeypatch, {})
    clone = _setup_repo(tmp_path, {
        "pytest.ini": "[pytest]\naddopts = --strict-markers\n",
        "tests/test_plain.py": "def test_plain():\n    assert True\n",
    })
    _commit(clone, {"tests/test_plain.py": _plain_fail_test()})

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.pytest_failure"
    assert violations[0].severity == "fail"


def test_empty_marker_config_blocks_marked_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7415 AC5 — empty marker disables warn path; default loads readme_contract."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "phases": _MINIMAL_PHASES,
            "tests": {"flaky_reruns": 0, "readme_contract_marker": ""},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    clone = _setup_repo(tmp_path, {
        "pyproject.toml": _PYTEST_MARKERS,
        "tests/test_readme.py": (
            "import pytest\n\n"
            "@pytest.mark.readme_contract\n"
            "def test_doc():\n"
            "    assert True\n"
        ),
    })
    _commit(clone, {"tests/test_readme.py": _marked_fail_test()})

    violations = TestsGate(clone, base_branch="main").check("", [])

    assert len(violations) == 1
    assert violations[0].rule_id == "tests.pytest_failure"
    assert violations[0].severity == "fail"

    default_cfg = tmp_path / "issuesmith-default.yaml"
    default_cfg.write_text(
        yaml.safe_dump({"repo": "example/repo", "phases": _MINIMAL_PHASES}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(default_cfg))
    reset_config_cache()
    assert load_config().tests.readme_contract_marker == "readme_contract"
