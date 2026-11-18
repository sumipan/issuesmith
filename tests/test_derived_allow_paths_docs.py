"""Derived allow_paths for repository docs named in failing tests (#7125)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates.worktree import (
    TestsGate,
    check_derived_test_guard,
    derive_doc_allow_paths,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False,
    )


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        fp = root / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")


def _commit_all(root: Path, message: str = "update") -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message)


def _make_repo(tmp_path: Path, base_files: dict[str, str]) -> Path:
    seed = tmp_path / "seed"
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    subprocess.run(["git", "init", "-b", "main", str(seed)], capture_output=True, check=True)
    _git(seed, "config", "user.email", "t@t.com")
    _git(seed, "config", "user.name", "T")
    _write(seed, {"conftest.py": "", **base_files})
    _commit_all(seed, "init")
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)], capture_output=True, check=True
    )
    subprocess.run(["git", "clone", str(origin), str(clone)], capture_output=True, check=True)
    _git(clone, "config", "user.email", "t@t.com")
    _git(clone, "config", "user.name", "T")
    _git(clone, "checkout", "-b", "feat")
    return clone


_DOCS_TEST = (
    "from pathlib import Path\n\n"
    "ROOT = Path(__file__).resolve().parents[2]\n"
    "_README = ROOT / \"README.md\"\n"
    "_GUIDE = ROOT / \"docs\" / \"GUIDE.md\"\n\n\n"
    "def test_x():\n"
    "    assert _README.is_file()\n"
    "    assert _GUIDE.is_file()\n"
)

_EXCLUSION_TEST = (
    "from pathlib import Path\n\n"
    "ROOT = Path(__file__).resolve().parents[2]\n\n\n"
    "def test_x():\n"
    "    assert (ROOT / \"pyproject.toml\").is_file()\n"
    "    assert not (ROOT / \"MISSING.md\").exists()\n"
    "    assert (ROOT / \"README.md\").is_file()\n"
)


@pytest.fixture()
def _derived_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def _set(enabled: bool | None) -> None:
        data: dict = {"repo": "example/repo"}
        if enabled is not None:
            data["derived_allow"] = {"enabled": enabled}
        cfg = tmp_path / "issuesmith.yaml"
        cfg.write_text(yaml.safe_dump(data), encoding="utf-8")
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
        reset_config_cache()

    yield _set
    reset_config_cache()


def test_failing_test_naming_docs_derives_them(tmp_path: Path) -> None:
    """#7125: failing tests that name .md paths extend derived_allow_paths."""
    clone = _make_repo(tmp_path, {
        "README.md": "# readme\n",
        "docs/GUIDE.md": "# guide\n",
        "src/pkg/mod.py": "x = 1\n",
        "tests/conventions/test_docs.py": _DOCS_TEST,
    })
    derived = derive_doc_allow_paths(
        clone,
        "main",
        ["tests/conventions/test_docs.py::test_x"],
        ["src/pkg/mod.py"],
    )
    assert derived == ["README.md", "docs/GUIDE.md"]


def test_non_markdown_and_missing_paths_are_not_derived(tmp_path: Path) -> None:
    """#7125: non-.md, missing-on-base, and allow_paths hits are excluded."""
    clone = _make_repo(tmp_path, {
        "README.md": "# readme\n",
        "pyproject.toml": "[project]\nname = \"x\"\n",
        "src/pkg/mod.py": "",
        "tests/conventions/test_docs.py": _EXCLUSION_TEST,
    })
    derived = derive_doc_allow_paths(
        clone,
        "main",
        ["tests/conventions/test_docs.py::test_x"],
        ["src/pkg/mod.py", "README.md"],
    )
    assert derived == []


def test_guard_skips_derived_markdown(tmp_path: Path) -> None:
    """#7125: derived .md files are not subject to derived test-shape guard."""
    clone = _make_repo(tmp_path, {
        "README.md": "# readme\n",
        "tests/conventions/test_docs.py": _DOCS_TEST,
    })
    _write(clone, {"README.md": "# readme\nextra line\n"})
    _commit_all(clone, "edit readme")
    assert check_derived_test_guard(clone, "main", ["README.md"]) == []


def test_tests_gate_derive_includes_named_docs(
    tmp_path: Path, _derived_config
) -> None:
    """#7125: TestsGate._derive unions doc paths when derived_allow is enabled."""
    _derived_config(True)
    clone = _make_repo(tmp_path, {
        "README.md": "# readme\n",
        "docs/GUIDE.md": "# guide\n",
        "src/pkg/mod.py": "x = 1\n",
        "tests/conventions/test_docs.py": _DOCS_TEST,
    })
    gate = TestsGate(clone, base_branch="main", allow_paths=["src/pkg/mod.py"])
    derived = gate._derive(["tests/conventions/test_docs.py::test_x"])
    assert "README.md" in derived
    assert "docs/GUIDE.md" in derived
