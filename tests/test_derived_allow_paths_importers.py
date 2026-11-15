"""Derived allow_paths: direct importers and static derivation."""

from __future__ import annotations

from pathlib import Path

from issuesmith.gates.pr_scope import PrScopeGate
from issuesmith.gates.worktree import (
    derive_test_allow_paths,
)
from tests import test_derived_allow_paths as _base
from tests.test_derived_allow_paths import _TEST_USES_MOD, _commit_all, _make_repo, _write

_derived_config = _base._derived_config
ledger_repo = _base.ledger_repo
mod_repo = _base.mod_repo


# ---------------------------------------------------------------------------
# #5351: one-hop direct src importers extend condition (C) reference keys
# ---------------------------------------------------------------------------

_ENGINE_SPEC_BASE = "ENGINE_SPECS = {'cursor': 'agent'}\n"
_ENGINE_SPEC_CHANGED = "ENGINE_SPECS = {'cursor': 'cursor-agent'}\n"
_CONSUMER = (
    "from pkg.core.engine_spec import ENGINE_SPECS\n\n\n"
    "def classify(x):\n"
    "    return ENGINE_SPECS.get(x)\n"
)
_TEST_VIA_CONSUMER = (
    "from pkg.llm import failure_classification\n\n\n"
    "def test_specs():\n"
    "    assert failure_classification.classify('cursor') == 'agent'\n"
)


def _engine_spec_repo(tmp_path: Path, extra_base: dict[str, str] | None = None) -> Path:
    base: dict[str, str] = {
        "src/pkg/__init__.py": "",
        "src/pkg/core/__init__.py": "",
        "src/pkg/core/engine_spec.py": _ENGINE_SPEC_BASE,
        "src/pkg/llm/__init__.py": "",
        "src/pkg/llm/failure_classification.py": _CONSUMER,
        "tests/test_failure_classification.py": _TEST_VIA_CONSUMER,
    }
    if extra_base:
        base.update(extra_base)
    clone = _make_repo(tmp_path, base)
    _write(clone, {"src/pkg/core/engine_spec.py": _ENGINE_SPEC_CHANGED})
    _commit_all(clone, "change engine_spec")
    return clone


def test_5351_direct_importer_derives_consumer_test(tmp_path: Path) -> None:
    clone = _engine_spec_repo(tmp_path)
    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_failure_classification.py::test_specs"],
        ["src/pkg/core/engine_spec.py"],
        ["src/pkg/core/engine_spec.py"],
    )
    assert derived == ["tests/test_failure_classification.py"]








def _many_importers(count: int) -> dict[str, str]:
    files: dict[str, str] = {
        "src/pkg/__init__.py": "",
        "src/pkg/core/__init__.py": "",
        "src/pkg/core/engine_spec.py": _ENGINE_SPEC_BASE,
    }
    for i in range(count):
        pkg = f"src/pkg/importers/i{i}"
        files[f"{pkg}/__init__.py"] = ""
        files[f"{pkg}/mod.py"] = "from pkg.core.engine_spec import ENGINE_SPECS\n"
    return files




def test_5351_over_importer_limit_skips_importer_keys(tmp_path: Path) -> None:
    n = 51
    files = _many_importers(n)
    files["tests/test_importer_ref.py"] = (
        "import pkg.importers.i0.mod as consumer\n\n\n"
        "def test_ref():\n"
        "    assert consumer is not None\n"
    )
    clone = _make_repo(tmp_path, files)
    _write(clone, {"src/pkg/core/engine_spec.py": _ENGINE_SPEC_CHANGED})
    _commit_all(clone)
    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_importer_ref.py::test_ref"],
        ["src/pkg/core/engine_spec.py"],
        ["src/pkg/core/engine_spec.py"],
    )
    assert derived == []






# ---------------------------------------------------------------------------
# #5884: static derived allow_paths without pytest failure history
# ---------------------------------------------------------------------------


def test_static_pr_scope_referencing_test_passes_without_failure(
    mod_repo: Path, _derived_config,
) -> None:
    _derived_config(None)
    _write(mod_repo, {"tests/test_uses_mod.py": _TEST_USES_MOD.replace("== 3", "== 4")})
    gate = PrScopeGate(mod_repo, ["src/pkg/mod.py"], "main")
    assert gate.check("", []) == []




def test_static_pr_scope_new_test_not_on_base(mod_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(mod_repo, {"tests/test_new.py": "import pkg.mod\n\ndef test_x():\n    assert True\n"})
    _commit_all(mod_repo)
    gate = PrScopeGate(mod_repo, ["src/pkg/mod.py"], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pr_scope.out_of_allow"
    assert v.location == "tests/test_new.py"
    assert v.fix_hint == "widen:tests/test_new.py"
    assert v.message == "tests/test_new.py: outside allow_paths (not on base)"










