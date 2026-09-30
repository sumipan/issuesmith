"""LintGate fix/check behaviour with real ruff (#4259)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from issuesmith.gates.base import ContractInput
from issuesmith.gates.worktree import LintGate

_RUFF_TOML = '[lint]\nselect = ["F", "I"]\n'


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        check=True,
    )


@pytest.fixture()
def lint_worktree(tmp_path: Path) -> Path:
    """Bare origin + clone with ruff.toml; files committed ahead of origin/main."""
    if shutil.which("ruff") is None:
        pytest.skip("ruff not found")

    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@test.com")
    _git(origin, "config", "user.name", "Test")
    (origin / "README.md").write_text("hello\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "init")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin), str(clone)],
        capture_output=True,
        check=True,
    )
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")
    (clone / "ruff.toml").write_text(_RUFF_TOML, encoding="utf-8")
    _git(clone, "add", "ruff.toml")
    _git(clone, "commit", "-m", "add ruff.toml")
    return clone


def _commit_py(worktree: Path, rel: str, content: str) -> Path:
    path = worktree / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(worktree, "add", rel)
    _git(worktree, "commit", "-m", f"add {rel}")
    return path


def _gate(worktree: Path) -> LintGate:
    return LintGate(worktree, allow_paths=["**"], base_branch="main")


def test_fix_preserves_unused_import(lint_worktree: Path) -> None:
    path = _commit_py(lint_worktree, "src/foo.py", "import os\n\ndef foo(): pass\n")
    gate = _gate(lint_worktree)
    gate.fix(ContractInput(body=""))
    assert "import os" in path.read_text(encoding="utf-8")


def test_check_f401_not_auto_fixable_with_repair_hint(lint_worktree: Path) -> None:
    _commit_py(lint_worktree, "src/foo.py", "import os\n\ndef foo(): pass\n")
    gate = _gate(lint_worktree)
    violations = gate.check("", [])
    f401 = [v for v in violations if v.rule_id == "lint.F401"]
    assert len(f401) == 1
    assert f401[0].auto_fixable is False
    assert "__all__" in f401[0].message
    assert "# noqa: F401" in f401[0].message


def test_fix_i001_only(lint_worktree: Path) -> None:
    content = "import sys\nimport os\n\nprint(os.path, sys.version)\n"
    _commit_py(lint_worktree, "src/bar.py", content)
    gate = _gate(lint_worktree)
    violations = gate.check("", [])
    i001 = [v for v in violations if v.rule_id == "lint.I001"]
    assert len(i001) == 1
    assert i001[0].auto_fixable is True
    gate.fix(ContractInput(body=""))
    assert gate.check("", []) == []


def test_fix_i001_leaves_f401(lint_worktree: Path) -> None:
    content = "import sys\nimport os\n\nprint(sys.version)\n"
    _commit_py(lint_worktree, "src/both.py", content)
    gate = _gate(lint_worktree)
    gate.fix(ContractInput(body=""))
    violations = gate.check("", [])
    assert all(v.rule_id != "lint.I001" for v in violations)
    f401 = [v for v in violations if v.rule_id == "lint.F401"]
    assert len(f401) == 1


def test_f821_not_auto_fixable(lint_worktree: Path) -> None:
    _commit_py(lint_worktree, "src/undef.py", "x = undefined_name\n")
    gate = _gate(lint_worktree)
    violations = gate.check("", [])
    f821 = [v for v in violations if v.rule_id == "lint.F821"]
    assert len(f821) == 1
    assert f821[0].auto_fixable is False
    assert f821[0].fix_hint is None


def test_clean_file_no_violations(lint_worktree: Path) -> None:
    path = _commit_py(lint_worktree, "src/clean.py", "def clean() -> int:\n    return 1\n")
    original = path.read_text(encoding="utf-8")
    gate = _gate(lint_worktree)
    assert gate.check("", []) == []
    gate.fix(ContractInput(body=""))
    assert path.read_text(encoding="utf-8") == original
