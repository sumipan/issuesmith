"""Derived allow_paths: direct importers and static derivation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from issuesmith.gates.pr_scope import PrScopeGate
from issuesmith.gates.worktree import (
    derive_static_ledger_allow_paths,
    derive_static_test_allow_paths,
    derive_test_allow_paths,
)
from tests import test_derived_allow_paths as _base
from tests.test_derived_allow_paths import _LEDGER, _TEST_UNRELATED, _TEST_USES_MOD, _commit_all, _make_repo, _write

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


def test_5351_import_as_form_counts_as_direct_importer(tmp_path: Path) -> None:
    consumer = (
        "import pkg.core.engine_spec as engine_spec\n\n\n"
        "def classify(x):\n"
        "    return engine_spec.ENGINE_SPECS.get(x)\n"
    )
    clone = _engine_spec_repo(tmp_path, {
        "src/pkg/llm/failure_classification.py": consumer,
    })
    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_failure_classification.py::test_specs"],
        ["src/pkg/core/engine_spec.py"],
        ["src/pkg/core/engine_spec.py"],
    )
    assert derived == ["tests/test_failure_classification.py"]


def test_5351_no_importer_key_match_returns_empty(tmp_path: Path) -> None:
    clone = _engine_spec_repo(tmp_path, {
        "tests/test_failure_classification.py": "def test_specs():\n    assert 1 == 2\n",
    })
    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_failure_classification.py::test_specs"],
        ["src/pkg/core/engine_spec.py"],
        ["src/pkg/core/engine_spec.py"],
    )
    assert derived == []


def test_5351_two_hop_importer_not_derived(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {
        "src/pkg/__init__.py": "",
        "src/pkg/core/__init__.py": "",
        "src/pkg/core/engine_spec.py": _ENGINE_SPEC_BASE,
        "src/pkg/llm/__init__.py": "",
        "src/pkg/llm/failure_classification.py": _CONSUMER,
        "src/pkg/metrics/__init__.py": "",
        "src/pkg/metrics/parsers.py": (
            "from pkg.llm.failure_classification import classify\n\n\n"
            "def parse_metrics():\n"
            "    return classify('cursor')\n"
        ),
        "tests/test_metrics_parsers.py": (
            "from pkg.metrics import parsers\n\n\n"
            "def test_parse():\n"
            "    assert parsers.parse_metrics() == 'agent'\n"
        ),
    })
    _write(clone, {"src/pkg/core/engine_spec.py": _ENGINE_SPEC_CHANGED})
    _commit_all(clone)
    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_metrics_parsers.py::test_parse"],
        ["src/pkg/core/engine_spec.py"],
        ["src/pkg/core/engine_spec.py"],
    )
    assert derived == []


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


def test_5351_at_importer_limit_adds_keys(tmp_path: Path) -> None:
    n = 50
    files = _many_importers(n)
    stem = "i0"
    files["tests/test_importer_ref.py"] = (
        f"import pkg.importers.{stem}.mod as consumer\n\n\n"
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
    assert derived == ["tests/test_importer_ref.py"]


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


def test_5351_tests_dir_import_not_counted_as_importer(tmp_path: Path) -> None:
    clone = _engine_spec_repo(tmp_path, {
        "src/pkg/llm/failure_classification.py": "VALUE = 1\n",
        "tests/test_only_importer.py": "from pkg.core.engine_spec import ENGINE_SPECS\n",
    })
    derived = derive_test_allow_paths(
        clone, "main",
        ["tests/test_failure_classification.py::test_specs"],
        ["src/pkg/core/engine_spec.py"],
        ["src/pkg/core/engine_spec.py"],
    )
    assert derived == []


def test_5351_grep_failure_falls_back_to_direct_keys_only(tmp_path: Path) -> None:
    clone = _engine_spec_repo(tmp_path)
    import issuesmith.gates.worktree as wt

    with patch.object(wt, "_direct_src_importers", return_value=set()):
        derived = derive_test_allow_paths(
            clone, "main",
            ["tests/test_failure_classification.py::test_specs"],
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


def test_static_pr_scope_weakened_and_skipped(mod_repo: Path, _derived_config) -> None:
    _derived_config(None)
    weakened = _TEST_USES_MOD.replace(
        "def test_total():\n    assert pkg.mod.compute_total(1, 2) == 3\n", ""
    )
    _write(mod_repo, {"tests/test_uses_mod.py": weakened})
    gate = PrScopeGate(mod_repo, ["src/pkg/mod.py"], "main")
    assert [v.rule_id for v in gate.check("", [])] == ["derived_allow.test_weakened"]

    skipped = _TEST_USES_MOD.replace(
        "def test_total():", "@pytest.mark.skip\n\ndef test_total():"
    )
    _write(mod_repo, {"tests/test_uses_mod.py": skipped})
    assert [v.rule_id for v in gate.check("", [])] == ["derived_allow.test_skipped"]


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


def test_static_pr_scope_unreferenced_test_message(mod_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(
        mod_repo,
        {"tests/test_unrelated.py": _TEST_UNRELATED + "\n\ndef test_more():\n    assert 2\n"},
    )
    gate = PrScopeGate(mod_repo, ["src/pkg/mod.py"], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pr_scope.out_of_allow"
    assert v.location == "tests/test_unrelated.py"
    assert v.message == (
        "tests/test_unrelated.py: outside allow_paths (no reference to changed files)"
    )


def test_static_pr_scope_ledger_without_failure(
    ledger_repo: Path, _derived_config,
) -> None:
    _derived_config(None)
    _write(ledger_repo, {_LEDGER: "alpha\nbeta\n"})
    gate = PrScopeGate(ledger_repo, ["src/pkg/gamma.py"], "main")
    assert gate.check("", []) == []

    _write(ledger_repo, {_LEDGER: "alpha\nbeta\ngamma\ndelta\n"})
    assert [v.rule_id for v in gate.check("", [])] == ["derived_allow.ledger_grew"]


def test_static_pr_scope_disabled_keeps_out_of_allow(mod_repo: Path, _derived_config) -> None:
    _derived_config(False)
    _write(mod_repo, {"tests/test_uses_mod.py": _TEST_USES_MOD.replace("== 3", "== 4")})
    gate = PrScopeGate(mod_repo, ["src/pkg/mod.py"], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pr_scope.out_of_allow"
    assert v.message == "tests/test_uses_mod.py: outside allow_paths"


def test_derive_test_allow_paths_delegates_to_static(mod_repo: Path) -> None:
    static = derive_static_test_allow_paths(
        mod_repo, "main", ["tests/test_uses_mod.py"], ["src/pkg/mod.py"], ["src/pkg/mod.py"]
    )
    assert static == ["tests/test_uses_mod.py"]
    derived = derive_test_allow_paths(
        mod_repo, "main",
        ["tests/test_uses_mod.py::test_total"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py"],
    )
    assert derived == static
    assert derive_test_allow_paths(
        mod_repo, "main",
        ["tests/test_unrelated.py::test_unrelated"],
        ["src/pkg/mod.py"],
        ["src/pkg/mod.py"],
    ) == []


def test_static_derive_functions_exported() -> None:
    import issuesmith.gates.worktree as wt
    from issuesmith.gates.worktree import (
        derive_static_test_allow_paths,
    )

    assert derive_static_test_allow_paths is wt.derive_static_test_allow_paths
    assert derive_static_ledger_allow_paths is wt.derive_static_ledger_allow_paths
    assert "derive_static_test_allow_paths" in wt.__all__
    assert "derive_static_ledger_allow_paths" in wt.__all__
