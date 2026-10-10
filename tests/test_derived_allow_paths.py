"""Tests for derived allow_paths of newly failing tests (#3756).

Fixtures use real git repos under tmp_path (``origin`` is a bare repo).
Only the TestsGate case runs pytest (condition B); others pass failed_ids directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates import GATE_REGISTRY, GateBuildContext
from issuesmith.gates.pr_scope import PrScopeGate
from issuesmith.gates.worktree import (
    TestsGate,
    check_derived_test_guard,
    derive_ledger_allow_paths,
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


# ---------------------------------------------------------------------------
# Engine: derived_allow_paths: is on a standalone line after repair (#4159)
# ---------------------------------------------------------------------------


def test_engine_derived_block_on_newline_after_repair_no_trailing_newline(capsys) -> None:
    """repair stdout ending without newline: derived_allow_paths: must be a standalone line."""
    from issuesmith.engine import _run_guarded_with_requires

    step_cfg_mock = MagicMock()
    step_cfg_mock.requires = []

    def fake_run_requires_loop(step_cfg, step_id, context):
        sys.stdout.write("PIPELINE_STATUS: REPAIR_DONE")  # intentionally no trailing newline
        context["derived_allow_paths"] = "tests/a.py"
        return None

    with (
        patch("issuesmith.ops.dispatch.resolve_step_config", return_value=step_cfg_mock),
        patch("issuesmith.engine._run_pre_gate_phase", return_value=None),
        patch("issuesmith.engine._run_emit_order", return_value=(0, "")),
        patch("issuesmith.ops.dispatch.run_requires_loop", fake_run_requires_loop),
    ):
        rc = _run_guarded_with_requires(
            "implementation", "tpl.md", [],
            success_statuses=["IMPL_DONE"],
            failure_status="IMPL_FAILED",
            cwd=None, tier=None,
            emit_status="IMPL_DONE",
            requires_step="p1",
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert "REPAIR_DONEderived_allow_paths:" not in out
    lines = out.splitlines()
    assert "derived_allow_paths:" in lines
    idx = lines.index("derived_allow_paths:")
    assert lines[idx + 1] == "  - tests/a.py"


def test_engine_emit_status_extractable_after_repair_no_trailing_newline(capsys) -> None:
    """PIPELINE_STATUS: IMPL_DONE is extractable as a standalone line after repair fix."""
    from issuesmith.engine import _extract_status_values, _run_guarded_with_requires

    step_cfg_mock = MagicMock()
    step_cfg_mock.requires = []

    def fake_run_requires_loop(step_cfg, step_id, context):
        sys.stdout.write("PIPELINE_STATUS: REPAIR_DONE")  # intentionally no trailing newline
        context["derived_allow_paths"] = "tests/a.py"
        return None

    with (
        patch("issuesmith.ops.dispatch.resolve_step_config", return_value=step_cfg_mock),
        patch("issuesmith.engine._run_pre_gate_phase", return_value=None),
        patch("issuesmith.engine._run_emit_order", return_value=(0, "")),
        patch("issuesmith.ops.dispatch.run_requires_loop", fake_run_requires_loop),
    ):
        rc = _run_guarded_with_requires(
            "implementation", "tpl.md", [],
            success_statuses=["IMPL_DONE"],
            failure_status="IMPL_FAILED",
            cwd=None, tier=None,
            emit_status="IMPL_DONE",
            requires_step="p1",
        )

    out = capsys.readouterr().out
    assert rc == 0
    assert "IMPL_DONE" in _extract_status_values(out)


# ---------------------------------------------------------------------------
# #4791: ratchet ledgers next to failing tests are derived and may only shrink
# ---------------------------------------------------------------------------

_LEDGER_GLOBS = ["tests/conventions/known_*.txt"]
_LEDGER = "tests/conventions/known_x.txt"
_LEDGER_BASE = "alpha\nbeta\ngamma\n"
_LEDGER_TEST = (
    "from pathlib import Path\n\n"
    "ROOT = Path(__file__).resolve().parents[2]\n\n\n"
    "def test_a():\n"
    "    found = sorted(p.stem for p in (ROOT / 'src' / 'pkg').glob('*.py')\n"
    "                   if p.stem != '__init__')\n"
    "    known = sorted((Path(__file__).parent / 'known_x.txt').read_text().split())\n"
    "    assert found == known\n"
)


@pytest.fixture()
def ledger_repo(tmp_path: Path) -> Path:
    """Ledger lists src/pkg modules; deleting gamma.py makes test_a newly fail."""
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/alpha.py": "",
        "src/pkg/beta.py": "",
        "src/pkg/gamma.py": "",
        _LEDGER: _LEDGER_BASE,
        "tests/conventions/test_x.py": _LEDGER_TEST,
        "tests/test_other.py": _TEST_UNRELATED,
    })
    _git(clone, "rm", "-q", "src/pkg/gamma.py")
    _commit_all(clone, "remove gamma")
    return clone


def test_ledger_derived_from_failing_test_dir(ledger_repo: Path) -> None:
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/conventions/test_x.py::test_a"],
        ["src/pkg/gamma.py"], _LEDGER_GLOBS,
    )
    assert derived == [_LEDGER]


def test_ledger_not_derived_for_other_dir(ledger_repo: Path) -> None:
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/test_other.py::test_unrelated"],
        ["src/pkg/gamma.py"], _LEDGER_GLOBS,
    )
    assert derived == []


def test_ledger_not_on_base_not_derived(ledger_repo: Path) -> None:
    _write(ledger_repo, {"tests/conventions/known_new.txt": "x\n"})
    _commit_all(ledger_repo, "add ledger")
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/conventions/test_x.py::test_a"],
        ["src/pkg/gamma.py"], ["tests/conventions/known_*.txt"],
    )
    assert derived == [_LEDGER]


def test_ledger_already_allowed_not_derived(ledger_repo: Path) -> None:
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/conventions/test_x.py::test_a"],
        ["src/pkg/gamma.py", "tests/conventions/*.txt"], _LEDGER_GLOBS,
    )
    assert derived == []


def test_ledger_tests_gate_derives_ledger(ledger_repo: Path, _derived_config) -> None:
    _derived_config(None)
    gate = TestsGate(ledger_repo, allow_paths=["src/pkg/gamma.py"])
    violations = gate.check("", [])
    assert [v.rule_id for v in violations] == ["tests.pytest_failure"]
    assert gate.derived_allow_paths == [_LEDGER]


def test_ledger_tests_gate_disabled(ledger_repo: Path, _derived_config) -> None:
    _derived_config(False)
    gate = TestsGate(ledger_repo, allow_paths=["src/pkg/gamma.py"])
    assert gate.check("", [])
    assert gate.derived_allow_paths == []


def _ledger_guard(root: Path, content: str) -> list:
    _write(root, {_LEDGER: content})
    return check_derived_test_guard(root, "main", [_LEDGER], ledger_globs=_LEDGER_GLOBS)


def test_ledger_guard_delete_line_ok(ledger_repo: Path) -> None:
    assert _ledger_guard(ledger_repo, "alpha\nbeta\n") == []


def test_ledger_guard_reorder_ok(ledger_repo: Path) -> None:
    assert _ledger_guard(ledger_repo, "gamma\n  alpha\nbeta\n\n") == []


def test_ledger_guard_missing_file_ok(ledger_repo: Path) -> None:
    (ledger_repo / _LEDGER).unlink()
    assert check_derived_test_guard(
        ledger_repo, "main", [_LEDGER], ledger_globs=_LEDGER_GLOBS
    ) == []


def test_ledger_guard_added_line_grew(ledger_repo: Path) -> None:
    violations = _ledger_guard(ledger_repo, "alpha\nbeta\ndelta\n")
    assert [v.rule_id for v in violations] == ["derived_allow.ledger_grew"]
    assert violations[0].severity == "fail"
    assert violations[0].auto_fixable is False
    assert violations[0].location == _LEDGER
    assert "delta" in violations[0].message


def test_ledger_guard_without_globs_is_not_parsed_as_ledger(ledger_repo: Path) -> None:
    # The indented line is not valid Python: without ledger_globs the AST path reports
    # test_weakened; with them it is a reordered ledger and passes.
    content = "beta\n  alpha\n"
    _write(ledger_repo, {_LEDGER: content})
    violations = check_derived_test_guard(ledger_repo, "main", [_LEDGER])
    assert [v.rule_id for v in violations] == ["derived_allow.test_weakened"]
    assert "cannot parse working tree file" in violations[0].message
    assert _ledger_guard(ledger_repo, content) == []


def test_ledger_pr_scope_delete_only_passes(ledger_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(ledger_repo, {_LEDGER: "alpha\nbeta\n"})
    gate = PrScopeGate(
        ledger_repo, ["src/pkg/gamma.py"], "main", derived_allow_paths=[_LEDGER],
    )
    assert gate.check("", []) == []


def test_ledger_pr_scope_added_line_grew(ledger_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(ledger_repo, {_LEDGER: "alpha\nbeta\ngamma\ndelta\n"})
    gate = PrScopeGate(
        ledger_repo, ["src/pkg/gamma.py"], "main", derived_allow_paths=[_LEDGER],
    )
    assert [v.rule_id for v in gate.check("", [])] == ["derived_allow.ledger_grew"]


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


def test_5134_untracked_added_file_derives_dir_glob_test(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "workflows/base.yml": "name: base\n",
        "tests/workflows/test_names.py": _TEST_GLOBS_WORKFLOWS,
    })
    _write(clone, {"workflows/wsl.yml": "name: wsl\n"})

    derived = _derive_one(clone, "tests/workflows/test_names.py", ["workflows/wsl.yml"])
    assert derived == ["tests/workflows/test_names.py"]


def test_5134_deleted_file_derives_dir_glob_test(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "workflows/base.yml": "name: base\n",
        "workflows/legacy.yml": "name: legacy\n",
        "tests/workflows/test_names.py": _TEST_GLOBS_WORKFLOWS,
    })
    (clone / "workflows/legacy.yml").unlink()

    assert _derive_one(clone, "tests/workflows/test_names.py", []) == [
        "tests/workflows/test_names.py"
    ]


def test_5134_rename_derives_both_source_and_dest_dirs(tmp_path: Path) -> None:
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


def test_5134_modified_only_adds_no_parent_dir_key(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "workflows/tasks.yml": "name: tasks\n",
        "tests/workflows/test_names.py": _TEST_GLOBS_WORKFLOWS,
    })
    _write(clone, {"workflows/tasks.yml": "name: tasks2\n"})

    assert _derive_one(clone, "tests/workflows/test_names.py", ["workflows/tasks.yml"]) == []


def test_5134_root_level_add_and_delete_adds_no_empty_or_dot_key(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _parent_dir_keys, _reference_keys

    clone = _make_repo(tmp_path, {"setup.cfg": "[x]\n"})
    (clone / "setup.cfg").unlink()
    _write(clone, {"pyproject.toml": "[project]\n"})

    substr_keys, _ = _reference_keys(clone, "main", ["pyproject.toml"])
    assert "" not in substr_keys
    assert "." not in substr_keys
    assert _parent_dir_keys({"pyproject.toml", "./setup.cfg", "a/b/c.yml"}) == {"a/b"}


def test_5134_structural_change_paths_statuses(tmp_path: Path) -> None:
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


def test_5134_structural_change_paths_git_failure_is_tolerated(tmp_path: Path) -> None:
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


def test_5134_function_body_constant_change_derives_co_consts_test(tmp_path: Path) -> None:
    clone = _extractor_repo(tmp_path, (
        "from pkg.extractor import extract\n\n\n"
        "def test_consts():\n"
        "    assert \"alpha\" in extract.__code__.co_consts\n"
    ))
    assert _derive_extractor(clone) == ["tests/test_extractor.py"]


def test_5134_enclosing_name_is_the_only_matching_key(tmp_path: Path) -> None:
    # Only `extract_terms` links the test to the change: no path, module or stem in the text.
    # (`extract` itself is a generic symbol rejected by _is_valid_key.)
    test_text = (
        "from pkg.api import extract_terms\n\n\n"
        "def test_consts():\n"
        "    assert \",\" in extract_terms.__code__.co_consts\n"
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


def test_5134_hunk_enclosing_names_nested_and_filters(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _hunk_enclosing_names

    changed = (
        _NESTED_BASE.replace("y + 1", "y + 2")
        .replace("return 1\n", "return 10\n")
        .replace("return 2\n", "return 20\n")
    )
    clone = _nested_repo(tmp_path, changed)

    names = _hunk_enclosing_names(clone, "main", "src/pkg/nested.py")
    assert names == {"Processor", "handle_item", "inner_step", "nested_public"}


def test_5134_hunk_enclosing_names_module_level_only(tmp_path: Path) -> None:
    from issuesmith.gates.worktree import _hunk_enclosing_names

    clone = _nested_repo(tmp_path, _NESTED_BASE.replace("VALUE = 1", "VALUE = 2"))
    assert _hunk_enclosing_names(clone, "main", "src/pkg/nested.py") == set()


def test_5134_hunk_enclosing_names_degrades_to_empty(tmp_path: Path) -> None:
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


def test_5134_hunk_enclosing_names_bad_hunk_header(tmp_path: Path) -> None:
    from issuesmith.gates import worktree as wt

    clone = _nested_repo(tmp_path, _NESTED_BASE.replace("y + 1", "y + 2"))
    bogus = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="@@ garbage @@\n+    return y + 2\n", stderr=""
    )
    with patch.object(wt.subprocess, "run", return_value=bogus):
        assert wt._hunk_enclosing_names(clone, "main", "src/pkg/nested.py") == set()


def test_5134_syntax_error_keeps_existing_keys(tmp_path: Path) -> None:
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
