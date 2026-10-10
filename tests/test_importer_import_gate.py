"""importer_import gate: importers of changed modules must import without test stubs (#5013)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from issuesmith.gates import worktree as wt
from issuesmith.gates.worktree import ImporterImportGate, _dotted_module_name

_LABELS = "def andon():\n    return 'andon'\n\n\ndef finish():\n    return 'finish'\n"
_SYNC = "from pkg.labels import andon\n\n\ndef run():\n    return andon()\n"
_TEST_SYNC_STUB = (
    "import sys\n"
    "import types\n"
    "\n"
    "import pkg.labels as _labels_module\n"
    "\n"
    "_labels_module.andon = lambda: 'stub'\n"
    "sys.modules.setdefault('pkg.fake', types.ModuleType('pkg.fake'))\n"
    "\n"
    "from pkg.steps import sync  # noqa: E402\n"
    "\n"
    "\n"
    "def test_sync():\n"
    "    assert sync.run() == 'stub'\n"
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_all(root: Path, msg: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", msg)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """bare origin + clone; main has pkg/labels.py (andon) and pkg/steps/sync.py importing it."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "test@test.com")
    _git(seed, "config", "user.name", "Test")
    _write(seed, "README.md", "hello\n")
    _write(seed, "pkg/__init__.py", "")
    _write(seed, "pkg/labels.py", _LABELS)
    _write(seed, "pkg/steps/__init__.py", "")
    _write(seed, "pkg/steps/sync.py", _SYNC)
    _commit_all(seed, "init")

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)],
        capture_output=True, check=True,
    )
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], capture_output=True, check=True)
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "checkout", "-b", "feat")
    return clone


def _remove_andon(root: Path) -> None:
    _write(root, "pkg/labels.py", "def finish():\n    return 'finish'\n")


def _check(root: Path) -> list:
    return ImporterImportGate(root, "main").check("", [])


def test_registered_in_gate_registry() -> None:
    from issuesmith.gates import GATE_REGISTRY

    entry = GATE_REGISTRY["importer_import"]
    assert entry.input_kind == "worktree"
    assert entry.repairable is True
    assert entry.pre_llm is False


def test_dotted_module_name() -> None:
    assert _dotted_module_name("tools/corklab/labels.py") == "tools.corklab.labels"
    assert _dotted_module_name("src/pkg/a/__init__.py") == "pkg.a"
    assert _dotted_module_name("pkg/__init__.py") == "pkg"
    assert _dotted_module_name("README.md") is None


def test_removed_symbol_flags_importer(repo: Path) -> None:
    _remove_andon(repo)
    _commit_all(repo, "drop andon")

    violations = _check(repo)

    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "importer_import.import_error"
    assert v.location == "pkg/steps/sync.py"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "andon" in v.message


def test_fixed_importer_has_no_violation(repo: Path) -> None:
    _remove_andon(repo)
    _write(repo, "pkg/steps/sync.py", "from pkg.labels import finish\n\n\ndef run():\n    return finish()\n")
    _commit_all(repo, "drop andon and fix importer")

    assert _check(repo) == []


def test_test_stub_does_not_hide_violation(repo: Path) -> None:
    _remove_andon(repo)
    _write(repo, "tests/test_sync.py", _TEST_SYNC_STUB)
    _commit_all(repo, "drop andon, stub in tests")

    violations = _check(repo)

    assert [v.location for v in violations] == ["pkg/steps/sync.py"]
    assert violations[0].rule_id == "importer_import.import_error"


def test_deleted_module_flags_importer(repo: Path) -> None:
    _git(repo, "rm", "-q", "pkg/labels.py")
    _git(repo, "commit", "-m", "delete labels")

    violations = _check(repo)

    assert len(violations) == 1
    assert violations[0].rule_id == "importer_import.import_error"
    assert violations[0].location == "pkg/steps/sync.py"


def test_uncommitted_importer_fix_is_seen(repo: Path) -> None:
    _remove_andon(repo)
    _commit_all(repo, "drop andon")
    _write(repo, "pkg/steps/sync.py", "from pkg.labels import finish\n\n\ndef run():\n    return finish()\n")

    assert _check(repo) == []


def test_non_python_changes_skip_import(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(repo, "README.md", "changed\n")
    _write(repo, "conf.yml", "a: 1\n")
    _commit_all(repo, "docs only")

    real_run = subprocess.run
    calls: list[list[str]] = []

    def recording_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(wt.subprocess, "run", recording_run)

    assert _check(repo) == []
    assert [c for c in calls if c and c[0] == sys.executable] == []


def test_timeout_is_not_violation(
    repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _remove_andon(repo)
    _commit_all(repo, "drop andon")

    real_run = subprocess.run

    def timeout_run(argv, *args, **kwargs):
        if argv and argv[0] == sys.executable:
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout", 60))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(wt.subprocess, "run", timeout_run)

    assert _check(repo) == []
    assert "importer_import: timeout pkg.steps.sync" in capsys.readouterr().err


def test_side_effect_failure_is_skipped(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(repo, "pkg/labels.py", _LABELS + "\n\nVERSION = 2\n")
    _write(
        repo, "pkg/cli.py",
        "from pkg.labels import andon\n\nraise RuntimeError('needs config')\n",
    )
    _commit_all(repo, "touch labels, add side-effect cli")

    assert _check(repo) == []
    assert "importer_import: skipped pkg.cli" in capsys.readouterr().err


def test_fix_returns_input_unchanged(repo: Path) -> None:
    sentinel = object()
    assert ImporterImportGate(repo, "main").fix(sentinel) is sentinel  # type: ignore[arg-type]
