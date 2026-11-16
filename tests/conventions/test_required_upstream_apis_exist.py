"""Every ghdag name issuesmith uses must exist in the installed ghdag (the pinned version in CI)."""
from __future__ import annotations

import importlib
import types
from typing import Callable

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


def test_doctor_no_longer_lists_removed_defer_apis():
    """ghdag 0.106.0 removed QuotaGate.defer / release_ready and DONE_DEFERRED (sumipan/nexus#5757)."""
    assert ("ghdag.quota", "QuotaGate", "defer") not in REQUIRED_UPSTREAM_APIS
    assert ("ghdag.quota", "QuotaGate", "release_ready") not in REQUIRED_UPSTREAM_APIS
def test_doctor_lists_quota_gate_public_state_apis():
    assert ("ghdag.quota", "QuotaGate", "read_state") in REQUIRED_UPSTREAM_APIS
    assert ("ghdag.quota", "QuotaGate", "modify") in REQUIRED_UPSTREAM_APIS


def test_detects_missing_quota_gate_read_state():
    old_ghdag = types.SimpleNamespace(
        QuotaGate=type(
            "QuotaGate",
            (),
            {"modify": lambda self, fn: {}},
        ),
    )
    missing = missing_upstream_apis(
        (("ghdag.quota", "QuotaGate", "read_state"), ("ghdag.quota", "QuotaGate", "modify")),
        resolver=lambda module: old_ghdag,
    )
    assert missing == ["ghdag.quota.QuotaGate.read_state"]


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


def _resolver_with_all_default_upstream_apis(*, dag: object) -> Callable[[str], object]:
    quota_gate = type(
        "QuotaGate",
        (),
        {"read_state": lambda self: {}, "modify": lambda self, fn: {}},
    )
    ghdag_quota = types.SimpleNamespace(QuotaGate=quota_gate)
    ghdag_forge = types.SimpleNamespace(
        GitHubClient=type("GitHubClient", (), {"graphql": lambda self, q: {}}),
        get_forge=lambda: None,
    )

    def resolver(module: str) -> object:
        if module == "ghdag.quota":
            return ghdag_quota
        if module == "ghdag.forge":
            return ghdag_forge
        if module == "ghdag.dag":
            return dag
        raise ImportError(module)

    return resolver


def test_ghdag_dag_without_wait_quota_companion_re_is_not_missing():
    """refs #7080: doctor list does not require ghdag.dag.WAIT_QUOTA_COMPANION_RE."""
    ghdag_dag = types.SimpleNamespace(
        PIPELINE_WAIT_QUOTA="WAIT",
        plan_recover=lambda: [],
    )
    resolver = _resolver_with_all_default_upstream_apis(dag=ghdag_dag)
    assert missing_upstream_apis(resolver=resolver) == []


def test_default_list_reports_absent_plan_recover():
    """refs #7080: default list still reports a missing runtime API name."""
    ghdag_dag = types.SimpleNamespace(PIPELINE_WAIT_QUOTA="WAIT")
    resolver = _resolver_with_all_default_upstream_apis(dag=ghdag_dag)
    assert missing_upstream_apis(resolver=resolver) == ["ghdag.dag.plan_recover"]
