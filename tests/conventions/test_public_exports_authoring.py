"""Public export contract for step/gate authoring modules (sumipan/nexus#6558)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import issuesmith.config as config_mod
import issuesmith.contract as contract_mod
import issuesmith.gates as gates_mod
from issuesmith import config as issuesmith_config
from issuesmith import contract as issuesmith_contract
from issuesmith import gates as issuesmith_gates
from issuesmith.config import StepConfig as config_step_config
from issuesmith.context_hook import (
    MetadataViolation as hook_metadata_violation,
)
from issuesmith.context_hook import (
    parse_issue_metadata as hook_parse_issue_metadata,
)
from issuesmith.context_hook import (
    validate_issue_metadata as hook_validate_issue_metadata,
)
from issuesmith.gate_registry import Violation as registry_violation
from issuesmith.gates.base import Gate as base_gate
from issuesmith.gates.worktree import external_target_state as worktree_external_target_state
from issuesmith.language import LanguagePack as language_language_pack
from issuesmith.pr_scope import normalize_allow_path as pr_scope_normalize_allow_path
from issuesmith.preconditions import PreconditionContext as preconditions_context
from tests.conventions._util import REPO_ROOT

CONTRACT_PUBLIC_NAMES = frozenset(
    {
        "StepContext",
        "StepResult",
        "StepConfig",
        "Andon",
        "RetrySignal",
        "RetryReason",
        "parse_issue_metadata",
        "validate_issue_metadata",
        "MetadataViolation",
    }
)

GATES_PUBLIC_NAMES = frozenset(
    {
        "Verdict",
        "GateBuildContext",
        "GateBuildError",
        "GateEntry",
        "RequiresGate",
        "GATE_REGISTRY",
        "NON_REPAIRABLE_RULE_IDS",
        "is_non_repairable_rule_id",
        "resolve_gate",
        "validate_step_requires",
        "check_scope",
        "check_pr_scope",
        "check_ac_complete",
        "check_deps",
        "Violation",
        "Gate",
        "PreconditionContext",
        "normalize_allow_path",
        "external_target_state",
    }
)

CONFIG_PUBLIC_NAMES = frozenset({"get_config", "load_config", "IssuesmithConfig", "LanguagePack"})


def test_contract_public_exports():
    """issuesmith.contract __all__ matches the authoring contract and re-exports are canonical."""
    assert frozenset(contract_mod.__all__) == CONTRACT_PUBLIC_NAMES
    for name in CONTRACT_PUBLIC_NAMES:
        assert hasattr(issuesmith_contract, name), f"contract missing attribute {name}"
    assert issuesmith_contract.StepConfig is config_step_config
    assert issuesmith_contract.parse_issue_metadata is hook_parse_issue_metadata
    assert issuesmith_contract.validate_issue_metadata is hook_validate_issue_metadata
    assert issuesmith_contract.MetadataViolation is hook_metadata_violation
    import issuesmith.engine as engine_mod

    assert issuesmith_contract.RetrySignal is engine_mod.RetrySignal
    assert issuesmith_contract.RetryReason is engine_mod.RetryReason


def test_gates_public_exports():
    """issuesmith.gates __all__ matches the gate authoring contract; five new names re-export."""
    assert frozenset(gates_mod.__all__) == GATES_PUBLIC_NAMES
    for name in GATES_PUBLIC_NAMES:
        assert hasattr(issuesmith_gates, name), f"gates missing attribute {name}"
    assert issuesmith_gates.Violation is registry_violation
    assert issuesmith_gates.Gate is base_gate
    assert issuesmith_gates.PreconditionContext is preconditions_context
    assert issuesmith_gates.normalize_allow_path is pr_scope_normalize_allow_path
    assert issuesmith_gates.external_target_state is worktree_external_target_state


def test_config_public_exports():
    """issuesmith.config __all__ matches the config contract; LanguagePack is from language."""
    assert frozenset(config_mod.__all__) == CONFIG_PUBLIC_NAMES
    for name in CONFIG_PUBLIC_NAMES:
        assert hasattr(issuesmith_config, name), f"config missing attribute {name}"
    assert issuesmith_config.LanguagePack is language_language_pack


def test_contract_import_has_no_cycle():
    """Fresh interpreter can import issuesmith.contract without ImportError."""
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-c", "import issuesmith.contract"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ImportError" not in proc.stderr


def test_contract_metadata_reexports_match_context_hook_with_incomplete_config(tmp_path: Path):
    """Metadata re-exports stay identical to context_hook even when ISSUESMITH_CONFIG omits phases."""
    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text("repo: example/repo\n", encoding="utf-8")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "ISSUESMITH_CONFIG": str(incomplete),
    }
    code = (
        "import issuesmith.contract as c\n"
        "import issuesmith.context_hook as h\n"
        "assert c.parse_issue_metadata is h.parse_issue_metadata\n"
        "assert c.validate_issue_metadata is h.validate_issue_metadata\n"
        "assert c.MetadataViolation is h.MetadataViolation\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
