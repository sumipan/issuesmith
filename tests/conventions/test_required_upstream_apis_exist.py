"""Every ghdag name issuesmith uses must exist in the installed ghdag (the pinned version in CI)."""
from __future__ import annotations

import importlib
import types

import pytest

from issuesmith.ops.preflight import REQUIRED_UPSTREAM_APIS, missing_upstream_apis
from tests.conventions._util import SRC, import_froms, py_files


def _ghdag_imports() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path in py_files(SRC):
        for module, name, _ in import_froms(path):
            if module == "ghdag" or module.startswith("ghdag."):
                found.add((module, name))
    return found


@pytest.mark.parametrize("module,name", sorted(_ghdag_imports()))
def test_imported_ghdag_names_exist(module: str, name: str):
    mod = importlib.import_module(module)
    assert hasattr(mod, name), f"{module}.{name} is not provided by the installed ghdag"


def test_runtime_reached_apis_exist():
    """Names reached via getattr(..., None) / late import: the doctor list must resolve."""
    assert missing_upstream_apis() == []


def test_doctor_lists_quota_gate_defer():
    assert ("ghdag.quota", "QuotaGate", "defer") in REQUIRED_UPSTREAM_APIS


def test_detects_pinned_ghdag_without_defer():
    """Reproduction of sumipan/nexus#3515: a ghdag whose QuotaGate has no defer()."""
    old_ghdag = types.SimpleNamespace(QuotaGate=type("QuotaGate", (), {"release_ready": lambda self: []}))
    missing = missing_upstream_apis(
        (("ghdag.quota", "QuotaGate", "defer"), ("ghdag.quota", "QuotaGate", "release_ready")),
        resolver=lambda module: old_ghdag,
    )
    assert missing == ["ghdag.quota.QuotaGate.defer"]


def test_detects_missing_module_and_name():
    def resolver(module: str):
        if module == "ghdag.gone":
            raise ImportError(module)
        return types.SimpleNamespace()

    missing = missing_upstream_apis(
        (("ghdag.gone", "X", None), ("ghdag.quota", "Missing", None)), resolver=resolver
    )
    assert missing == ["ghdag.gone.X", "ghdag.quota.Missing"]
