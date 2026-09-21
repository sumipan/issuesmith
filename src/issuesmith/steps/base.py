from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepContext:
    issue_number: str
    base_branch: str
    handler_name: str
    is_cross_repo: str  # "true" | "false"
    target_clone_path: str
    source: str
    workflow_name: str
    m1_result_filename: str
    m1r_result_filename: str
    # context_hook / workflow vars (defaults keep m2_finalize callers compatible)
    worktree_path: str = ""
    target_worktree_path: str = ""
    branch: str = ""
    target_repo: str = ""
    allow_paths: str = ""
    diary_worktree_path: str = ""
    has_diary_changes: str = ""
    pipeline_id: str = ""
    diary_allow_paths: str = ""
    issue_repo: str = ""
    p1_result_filename: str = ""
    p2_result_filename: str = ""
    p3_result_filename: str = ""
    execution_constraints: str = ""


@dataclass
class StepResult:
    exit_code: int
    pipeline_status: str  # MERGE_DONE | MIGRATION_REQUIRED | MERGE_FAILED | CP2_*
    recovery: str | None = None

    def __post_init__(self) -> None:
        # R3 (nexus docs/ISSUESMITH.md ワークフロー設計規約): a step may only stop with a
        # status that the preflight parity table knows. Enforced at construction so a new
        # runtime-only stop cannot be added without declaring how (or why not) it is
        # caught before dispatch.
        from issuesmith.gate_rules import assert_preflight_parity

        assert_preflight_parity(self.pipeline_status)
