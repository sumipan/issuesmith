"""Tests for derived allow_paths of newly failing tests (#3756).

Fixtures use real git repos under tmp_path (``origin`` is a bare repo).
Only the TestsGate case runs pytest (condition B); others pass failed_ids directly.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates import GATE_REGISTRY, GateBuildContext
from issuesmith.gates.pr_scope import PrScopeGate
from issuesmith.gates.worktree import (
    TestsGate,
    check_derived_test_guard,
    derive_test_allow_paths,
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


_MOD_BASE = "def compute_total(a, b):\n    return a + b\n"
_MOD_CHANGED = "def compute_total(a, b):\n    return a + b + 1\n"
_TEST_USES_MOD = (
    "import pkg.mod\n\n\n"
    "def test_total():\n"
    "    assert pkg.mod.compute_total(1, 2) == 3\n"
)
_TEST_UNRELATED = "def test_unrelated():\n    assert 1 == 1\n"


def _make_repo(tmp_path: Path, base_files: dict[str, str]) -> Path:
    """Create bare origin with base_files on main; return a clone on branch feat."""
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


@pytest.fixture()
def mod_repo(tmp_path: Path) -> Path:
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/mod.py": _MOD_BASE,
        "tests/test_uses_mod.py": _TEST_USES_MOD,
        "tests/test_unrelated.py": _TEST_UNRELATED,
    })
    _write(clone, {"src/pkg/mod.py": _MOD_CHANGED})
    _commit_all(clone, "change mod")
    return clone


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


# ---------------------------------------------------------------------------
# AC-1: TestsGate computes derived_allow_paths for newly failing referencing tests
# ---------------------------------------------------------------------------


def test_ac1_tests_gate_derives_referencing_test(mod_repo: Path, _derived_config) -> None:
    _derived_config(None)
    gate = TestsGate(mod_repo, allow_paths=["src/pkg/mod.py"])
    violations = gate.check("", [])

    assert [v.rule_id for v in violations] == ["tests.pytest_failure"]
    assert gate.derived_allow_paths == ["tests/test_uses_mod.py"]
    assert "derived_allow: tests/test_uses_mod.py" in (violations[0].fix_hint or "")


def test_ac1_build_tests_passes_allow_paths(mod_repo: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=mod_repo, allow_paths=["src/pkg/mod.py"], base_branch="main"
    )
    gate = GATE_REGISTRY["tests"].build(ctx)
    assert isinstance(gate, TestsGate)
    assert gate.derived_allow_paths == []
    assert gate._allow_paths == ["src/pkg/mod.py"]


def test_ac1_derive_by_module_name_and_public_def(mod_repo: Path) -> None:
    derived = derive_test_allow_paths(
        mod_repo, "main",
        ["tests/test_uses_mod.py::test_total"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py"],
    )
    assert derived == ["tests/test_uses_mod.py"]


# ---------------------------------------------------------------------------
# AC-1b: condition C via path string
# ---------------------------------------------------------------------------


def test_ac1b_path_string_reference(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "templates/order.md": "hello\n",
        "tests/test_template.py": (
            "from pathlib import Path\n\n\n"
            "def test_template():\n"
            "    assert Path(\"templates/order.md\").read_text() == \"hello\\n\"\n"
        ),
    })
    _write(clone, {"templates/order.md": "bye\n"})
    _commit_all(clone)

    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_template.py::test_template"],
        ["templates/order.md"],
        ["templates/order.md"],
    )
    assert derived == ["tests/test_template.py"]


# ---------------------------------------------------------------------------
# AC-2: exclusions
# ---------------------------------------------------------------------------


def test_ac2_unrelated_test_not_derived(mod_repo: Path) -> None:
    derived = derive_test_allow_paths(
        mod_repo, "main",
        ["tests/test_unrelated.py::test_unrelated"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py"],
    )
    assert derived == []


def test_ac2_new_test_file_not_derived(mod_repo: Path) -> None:
    _write(mod_repo, {"tests/test_brand_new.py": "import pkg.mod\n"})
    _commit_all(mod_repo)
    derived = derive_test_allow_paths(
        mod_repo, "main",
        ["tests/test_brand_new.py::test_x"],
        ["src/pkg/mod.py", "tests/test_brand_new.py"],
        ["src/pkg/mod.py"],
    )
    assert derived == []


def test_ac2_already_allowed_test_not_derived(mod_repo: Path) -> None:
    derived = derive_test_allow_paths(
        mod_repo, "main",
        ["tests/test_uses_mod.py::test_total"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py", "tests/test_*.py"],
    )
    assert derived == []


def test_ac2_non_tests_file_not_derived(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/mod.py": _MOD_BASE,
        "checks/test_uses_mod.py": _TEST_USES_MOD,
    })
    _write(clone, {"src/pkg/mod.py": _MOD_CHANGED})
    _commit_all(clone)
    derived = derive_test_allow_paths(
        clone, "main",
        ["checks/test_uses_mod.py::test_total"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py"],
    )
    assert derived == []


def test_ac2_preexisting_failure_not_derived(tmp_path: Path, _derived_config) -> None:
    """A referencing test that already fails on base is preexisting → not derived."""
    _derived_config(None)
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/mod.py": _MOD_BASE,
        "tests/test_uses_mod.py": (
            "import pkg.mod\n\n\n"
            "def test_total():\n"
            "    assert pkg.mod.compute_total(1, 2) == 99\n"
        ),
    })
    _write(clone, {"src/pkg/mod.py": _MOD_CHANGED})
    _commit_all(clone)

    gate = TestsGate(clone, allow_paths=["src/pkg/mod.py"])
    assert gate.check("", []) == []
    assert gate.derived_allow_paths == []


def test_ac2_baseline_unavailable_yields_empty(
    mod_repo: Path, monkeypatch: pytest.MonkeyPatch, _derived_config
) -> None:
    _derived_config(None)
    import issuesmith.gates.worktree as wt

    monkeypatch.setattr(wt, "_baseline_failed_func_ids", lambda *a, **k: None)
    gate = TestsGate(mod_repo, allow_paths=["src/pkg/mod.py"])
    violations = gate.check("", [])
    assert violations  # still blocking
    assert gate.derived_allow_paths == []


# ---------------------------------------------------------------------------
# AC-3: PrScopeGate honours derived_allow_paths
# ---------------------------------------------------------------------------


def test_ac3_pr_scope_allows_derived(mod_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(mod_repo, {
        "tests/test_uses_mod.py": _TEST_USES_MOD.replace("== 3", "== 4"),
        "tests/test_unrelated.py": _TEST_UNRELATED + "\n\ndef test_more():\n    assert 2\n",
    })
    gate = PrScopeGate(
        mod_repo, ["src/pkg/mod.py"], "main",
        derived_allow_paths=["tests/test_uses_mod.py"],
    )
    violations = gate.check("", [])
    assert [(v.rule_id, v.location) for v in violations] == [
        ("pr_scope.out_of_allow", "tests/test_unrelated.py"),
    ]


def test_ac3_registry_pr_scope_passes_derived(mod_repo: Path) -> None:
    _write(mod_repo, {"tests/test_uses_mod.py": _TEST_USES_MOD.replace("== 3", "== 4")})
    ctx = GateBuildContext(
        worktree_path=mod_repo,
        allow_paths=["src/pkg/mod.py"],
        base_branch="main",
        derived_allow_paths=("tests/test_uses_mod.py",),
    )
    gate = GATE_REGISTRY["pr_scope"].build(ctx)
    assert gate.check("", []) == []


def test_ac3_pr_scope_default_has_no_derived(mod_repo: Path) -> None:
    _write(mod_repo, {"tests/test_uses_mod.py": _TEST_USES_MOD.replace("== 3", "== 4")})
    gate = PrScopeGate(mod_repo, ["src/pkg/mod.py"], "main")
    assert [v.location for v in gate.check("", [])] == ["tests/test_uses_mod.py"]


def test_ac3_pr_scope_derived_edit_is_guarded(mod_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(mod_repo, {"tests/test_uses_mod.py": "import pkg.mod\n"})
    gate = PrScopeGate(
        mod_repo, ["src/pkg/mod.py"], "main",
        derived_allow_paths=["tests/test_uses_mod.py"],
    )
    rule_ids = [v.rule_id for v in gate.check("", [])]
    assert rule_ids == ["derived_allow.test_weakened"]


# ---------------------------------------------------------------------------
# AC-4: guard against weakening tests
# ---------------------------------------------------------------------------

_GUARD_BASE = (
    "import pytest\n\n\n"
    "def test_a():\n"
    "    assert 1 == 1\n\n\n"
    "class TestGroup:\n"
    "    def test_b(self):\n"
    "        assert 2 == 2\n"
    "        assert 3 == 3\n"
)


@pytest.fixture()
def guard_repo(tmp_path: Path) -> Path:
    return _make_repo(tmp_path, {"tests/test_guard.py": _GUARD_BASE})


def _guard(root: Path, content: str) -> list:
    _write(root, {"tests/test_guard.py": content})
    return check_derived_test_guard(root, "main", ["tests/test_guard.py"])


def test_ac4_expectation_change_only_is_ok(guard_repo: Path) -> None:
    assert _guard(guard_repo, _GUARD_BASE.replace("3 == 3", "3 == 4")) == []


def test_ac4_removed_test_function_is_weakened(guard_repo: Path) -> None:
    content = _GUARD_BASE.replace("    def test_b(self):", "    def helper_b(self):")
    violations = _guard(guard_repo, content)
    assert [v.rule_id for v in violations] == ["derived_allow.test_weakened"]
    assert "test_b" in violations[0].message
    assert violations[0].severity == "fail"
    assert violations[0].auto_fixable is False


def test_ac4_removed_assert_is_weakened(guard_repo: Path) -> None:
    violations = _guard(guard_repo, _GUARD_BASE.replace("        assert 3 == 3\n", ""))
    assert [v.rule_id for v in violations] == ["derived_allow.test_weakened"]


def test_ac4_added_skip_marker_is_skipped(guard_repo: Path) -> None:
    content = _GUARD_BASE.replace(
        "def test_a():", "@pytest.mark.skip(reason=\"x\")\ndef test_a():"
    )
    assert [v.rule_id for v in _guard(guard_repo, content)] == ["derived_allow.test_skipped"]


def test_ac4_added_xfail_call_is_skipped(guard_repo: Path) -> None:
    content = _GUARD_BASE.replace(
        "def test_a():\n", "def test_a():\n    pytest.xfail(\"later\")\n"
    )
    assert [v.rule_id for v in _guard(guard_repo, content)] == ["derived_allow.test_skipped"]


def test_ac4_syntax_error_is_weakened(guard_repo: Path) -> None:
    violations = _guard(guard_repo, "def test_a(:\n")
    assert [v.rule_id for v in violations] == ["derived_allow.test_weakened"]


# ---------------------------------------------------------------------------
# AC-6: derived_allow.enabled: false restores legacy behaviour
# ---------------------------------------------------------------------------


def test_ac6_disabled_yields_no_derived(mod_repo: Path, _derived_config) -> None:
    _derived_config(False)
    gate = TestsGate(mod_repo, allow_paths=["src/pkg/mod.py"])
    violations = gate.check("", [])
    assert violations
    assert gate.derived_allow_paths == []
    assert "derived_allow:" not in (violations[0].fix_hint or "")


def test_ac6_disabled_pr_scope_ignores_derived(mod_repo: Path, _derived_config) -> None:
    _derived_config(False)
    _write(mod_repo, {"tests/test_uses_mod.py": _TEST_USES_MOD.replace("== 3", "== 4")})
    gate = PrScopeGate(
        mod_repo, ["src/pkg/mod.py"], "main",
        derived_allow_paths=["tests/test_uses_mod.py"],
    )
    assert [v.rule_id for v in gate.check("", [])] == ["pr_scope.out_of_allow"]
