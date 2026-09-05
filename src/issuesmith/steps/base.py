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


@dataclass
class StepResult:
    exit_code: int
    pipeline_status: str  # MERGE_DONE | MIGRATION_REQUIRED | MERGE_FAILED
    recovery: str | None = None
