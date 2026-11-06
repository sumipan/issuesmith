"""Derived allow_paths: added/deleted/renamed files and hunk enclosing names."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from issuesmith.gates.worktree import (
    derive_test_allow_paths,
)
from tests.test_derived_allow_paths import _MOD_BASE, _TEST_USES_MOD, _commit_all, _git, _make_repo, _write

# ---------------------------------------------------------------------------
# #5134: structural-change parent dirs and hunk-enclosing def/class names as keys
# ---------------------------------------------------------------------------

_TEST_GLOBS_WORKFLOWS = (
    "from pathlib import Path\n\n"
    "ROOT = Path(__file__).resolve().parents[2]\n\n\n"
    "def test_names():\n"
    "    names = sorted(p.stem for p in (ROOT / \"workflows\").glob(\"*.yml\"))\n"
    "    assert names == [\"base\"]\n"
)
_TEST_GLOBS_LEGACY = _TEST_GLOBS_WORKFLOWS.replace('"workflows"', '"legacy"')


def _derive_one(root: Path, test_path: str, changed: list[str]) -> list[str]:
    return derive_test_allow_paths(
        root, "main", [f"{test_path}::test_names"], changed, ["workflows/*"]
    )


def test_untracked_added_file_derives_dir_glob_test(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "workflows/base.yml": "name: base\n",
        "tests/workflows/test_names.py": _TEST_GLOBS_WORKFLOWS,
    })
    _write(clone, {"workflows/wsl.yml": "name: wsl\n"})

    derived = _derive_one(clone, "tests/workflows/test_names.py", ["workflows/wsl.yml"])
    assert derived == ["tests/workflows/test_names.py"]


def test_deleted_file_derives_dir_glob_test(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "workflows/base.yml": "name: base\n",
        "workflows/legacy.yml": "name: legacy\n",
        "tests/workflows/test_names.py": _TEST_GLOBS_WORKFLOWS,
    })
    (clone / "workflows/legacy.yml").unlink()

    assert _derive_one(clone, "tests/workflows/test_names.py", []) == [
        "tests/workflows/test_names.py"
    ]


def test_rename_derives_both_source_and_dest_dirs(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "legacy/tasks.yml": "name: moved\nsteps: [a, b, c]\n",
        "tests/test_legacy_dir.py": _TEST_GLOBS_LEGACY,
        "tests/test_workflows_dir.py": _TEST_GLOBS_WORKFLOWS,
    })
    (clone / "workflows").mkdir()
    _git(clone, "mv", "legacy/tasks.yml", "workflows/tasks.yml")
    _commit_all(clone, "rename")

    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_legacy_dir.py::test_names", "tests/test_workflows_dir.py::test_names"],
        ["workflows/tasks.yml"],
        [],
    )
    assert derived == ["tests/test_legacy_dir.py", "tests/test_workflows_dir.py"]


def test_modified_only_adds_no_parent_dir_key(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "workflows/tasks.yml": "name: tasks\n",
        "tests/workflows/test_names.py": _TEST_GLOBS_WORKFLOWS,
    })
    _write(clone, {"workflows/tasks.yml": "name: tasks2\n"})

    assert _derive_one(clone, "tests/workflows/test_names.py", ["workflows/tasks.yml"]) == []


def test_root_level_add_and_delete_adds_no_empty_or_dot_key(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _parent_dir_keys, _reference_keys

    clone = _make_repo(tmp_path, {"setup.cfg": "[x]\n"})
    (clone / "setup.cfg").unlink()
    _write(clone, {"pyproject.toml": "[project]\n"})

    substr_keys, _ = _reference_keys(clone, "main", ["pyproject.toml"])
    assert "" not in substr_keys
    assert "." not in substr_keys
    assert _parent_dir_keys({"pyproject.toml", "./setup.cfg", "a/b/c.yml"}) == {"a/b"}


def test_structural_change_paths_statuses(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _structural_change_paths

    clone = _make_repo(tmp_path, {
        "keep/mod.yml": "a: 1\n",
        "gone/old.yml": "b: 2\n",
        "src_dir/moved.yml": "name: moved\nsteps: [a, b, c]\n",
    })
    _write(clone, {"keep/mod.yml": "a: 2\n", "fresh/new.yml": "c: 3\n"})
    (clone / "gone/old.yml").unlink()
    (clone / "dst_dir").mkdir()
    _git(clone, "mv", "src_dir/moved.yml", "dst_dir/moved.yml")

    paths = _structural_change_paths(
        clone, "main", ["dst_dir/moved.yml", "fresh/new.yml", "keep/mod.yml"]
    )
    assert paths == {"fresh/new.yml", "gone/old.yml", "src_dir/moved.yml", "dst_dir/moved.yml"}


def test_structural_change_paths_git_failure_is_tolerated(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _structural_change_paths

    clone = _make_repo(tmp_path, {"workflows/base.yml": "x: 1\n"})
    assert _structural_change_paths(clone, "no-such-branch", ["workflows/base.yml"]) == set()


_EXTRACTOR_BASE = (
    "def extract(text):\n"
    "    return text.replace(\"alpha\", \"beta\")\n"
    "\n\n"
    "def extract_terms(text):\n"
    "    return text.split(\",\")\n"
)
_EXTRACTOR_CHANGED = (
    _EXTRACTOR_BASE.replace("\"alpha\"", "\"gamma\"").replace("\",\"", "\";\"")
)


def _extractor_repo(tmp_path: Path, test_text: str) -> Path:
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/api.py": "from pkg.extractor import extract, extract_terms  # noqa: F401\n",
        "src/pkg/extractor.py": _EXTRACTOR_BASE,
        "tests/test_extractor.py": test_text,
    })
    _write(clone, {"src/pkg/extractor.py": _EXTRACTOR_CHANGED})
    return clone


def _derive_extractor(clone: Path) -> list[str]:
    return derive_test_allow_paths(
        clone, "main",
        ["tests/test_extractor.py::test_consts"],
        ["src/pkg/extractor.py"],
        ["src/pkg/extractor.py"],
    )


def test_function_body_constant_change_derives_name_referencing_test(tmp_path: Path) -> None:
    clone = _extractor_repo(tmp_path, (
        "from pkg.extractor import extract\n\n\n"
        "def test_consts():\n"
        "    assert extract(\"alpha\") is not None\n"
    ))
    assert _derive_extractor(clone) == ["tests/test_extractor.py"]


def test_enclosing_name_is_the_only_matching_key(tmp_path: Path) -> None:
    # Only `extract_terms` links the test to the change: no path, module or stem in the text.
    # (`extract` itself is a generic symbol rejected by _is_valid_key.)
    test_text = (
        "from pkg.api import extract_terms\n\n\n"
        "def test_consts():\n"
        "    assert extract_terms(\"a,b\") is not None\n"
    )
    clone = _extractor_repo(tmp_path, test_text)
    assert _derive_extractor(clone) == ["tests/test_extractor.py"]

    from issuesmith.gates.worktree import _hunk_enclosing_names

    assert _hunk_enclosing_names(clone, "main", "src/pkg/extractor.py") == {"extract_terms"}


_NESTED_BASE = (
    "class Processor:\n"
    "    def handle_item(self, x):\n"
    "        def inner_step(y):\n"
    "            return y + 1\n"
    "        return inner_step(x)\n"
    "\n\n"
    "def _private_outer():\n"
    "    def nested_public():\n"
    "        return 1\n"
    "    return nested_public()\n"
    "\n\n"
    "def run():\n"
    "    return 2\n"
    "\n\n"
    "def unrelated_function():\n"
    "    return 3\n"
    "\n\n"
    "VALUE = 1\n"
)


def _nested_repo(tmp_path: Path, changed_content: str) -> Path:
    clone = _make_repo(tmp_path, {"src/pkg/nested.py": _NESTED_BASE})
    _write(clone, {"src/pkg/nested.py": changed_content})
    return clone


def test_hunk_enclosing_names_nested_and_filters(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _hunk_enclosing_names

    changed = (
        _NESTED_BASE.replace("y + 1", "y + 2")
        .replace("return 1\n", "return 10\n")
        .replace("return 2\n", "return 20\n")
    )
    clone = _nested_repo(tmp_path, changed)

    names = _hunk_enclosing_names(clone, "main", "src/pkg/nested.py")
    assert names == {"Processor", "handle_item", "inner_step", "nested_public"}


def test_hunk_enclosing_names_module_level_only(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _hunk_enclosing_names

    clone = _nested_repo(tmp_path, _NESTED_BASE.replace("VALUE = 1", "VALUE = 2"))
    assert _hunk_enclosing_names(clone, "main", "src/pkg/nested.py") == set()


def test_hunk_enclosing_names_degrades_to_empty(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _hunk_enclosing_names

    clone = _nested_repo(tmp_path, _NESTED_BASE.replace("y + 1", "y +"))
    # syntax error
    assert _hunk_enclosing_names(clone, "main", "src/pkg/nested.py") == set()
    # diff failure
    _write(clone, {"src/pkg/nested.py": _NESTED_BASE.replace("y + 1", "y + 2")})
    assert _hunk_enclosing_names(clone, "no-such-branch", "src/pkg/nested.py") == set()
    # non-.py / deleted file
    assert _hunk_enclosing_names(clone, "main", "src/pkg/missing.py") == set()
    (clone / "src/pkg/nested.py").unlink()
    assert _hunk_enclosing_names(clone, "main", "src/pkg/nested.py") == set()


def test_hunk_enclosing_names_bad_hunk_header(tmp_path: Path) -> None:
    from issuesmith.gates import worktree as wt

    clone = _nested_repo(tmp_path, _NESTED_BASE.replace("y + 1", "y + 2"))
    bogus = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="@@ garbage @@\n+    return y + 2\n", stderr=""
    )
    with patch.object(wt.subprocess, "run", return_value=bogus):
        assert wt._hunk_enclosing_names(clone, "main", "src/pkg/nested.py") == set()


def test_syntax_error_keeps_existing_keys(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/mod.py": _MOD_BASE,
        "tests/test_uses_mod.py": _TEST_USES_MOD,
    })
    _write(clone, {"src/pkg/mod.py": "def compute_total(a, b:\n"})

    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_uses_mod.py::test_total"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py"],
    )
    assert derived == ["tests/test_uses_mod.py"]
