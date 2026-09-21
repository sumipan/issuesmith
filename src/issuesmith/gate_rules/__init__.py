from ghdag.workflow.gates import GATE_REGISTRY, GateRule, Violation  # noqa: F401

import issuesmith.gate_rules.b1_ac_format  # noqa: E402,F401
import issuesmith.gate_rules.b1_migration  # noqa: E402,F401
import issuesmith.gate_rules.b1_milestone_subdesign  # noqa: E402,F401
import issuesmith.gate_rules.cp1  # noqa: E402,F401
import issuesmith.gate_rules.m2  # noqa: E402,F401
import issuesmith.gate_rules.milestone_consistency  # noqa: E402,F401
import issuesmith.gate_rules.scope_breadth  # noqa: E402,F401

# R3 — runtime stop ↔ preflight parity (nexus docs/ISSUESMITH.md ワークフロー設計規約).
# Every pipeline_status that stops a step at runtime must name the gate rule that
# catches the same defect before dispatch, or an explicit "runtime-only:" reason.
# tests/test_workflow_conventions.py fails when a step introduces a stop status
# that is missing here, or when a mapped rule_id does not exist.
# A pipeline_status is a *stop* status when it ends with one of these.
STOP_STATUS_SUFFIXES: tuple[str, ...] = ("_FAILED", "_ERROR", "_REQUIRED", "TOO_LARGE", "STALE_BASE")

PREFLIGHT_PARITY: dict[str, str] = {
    "SCOPE_TOO_LARGE": "scope_breadth.too_large",
    "MIGRATION_REQUIRED": "b1_migration.migration_procedure_missing",
    "IMPL_FAILED": "b1_milestone_subdesign.change_paths_unreadable",
    "SUB1_BODY_INIT_ERROR": "runtime-only: template expansion error, no body contract",
    "STALE_BASE": "runtime-only: base branch moved after draft",
    "WORKTREE_FAILED": "runtime-only: git worktree / clone failure",
    "MERGE_FAILED": "runtime-only: merge conflict or CI state at M1",
    "CP2_FAILED": "runtime-only: verifies the implementation diff; no body contract to preflight",
}


def is_stop_status(status: str) -> bool:
    return status.endswith(STOP_STATUS_SUFFIXES)


def assert_preflight_parity(status: str) -> None:
    """Structural guard (R3): a step cannot return a stop status that has no
    preflight parity entry. Called from steps.base.StepResult.__post_init__."""
    if is_stop_status(status) and status not in PREFLIGHT_PARITY:
        raise ValueError(
            f"pipeline_status {status!r} is a stop status with no preflight parity entry; "
            "add it to issuesmith.gate_rules.PREFLIGHT_PARITY with the gate rule_id that "
            "catches the defect before dispatch, or an explicit 'runtime-only:' reason"
        )
