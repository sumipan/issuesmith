from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from issuesmith.engine import RetrySignal


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
    repair_violations: str = ""
    repair_step_origin: str = ""


@dataclass
class Andon:
    """Lightweight Andon spec returned from step implementations.

    Dispatch constructs the full issuesmith.andon.Andon from context fields.
    """
    kind: str  # "decision" | "blocked" | "broken"
    summary: str = ""


@dataclass
class Verdict:
    """Gate verdict for irreversible step pre-checks."""
    passed: bool
    reason: str = ""


@dataclass
class StepResult:
    # New primary contract (3-value status)
    status: Literal["done", "retry", "andon"] = "done"
    markers: list[str] = field(default_factory=list)
    retry: RetrySignal | None = None
    andon: Andon | None = None
    artifacts: dict = field(default_factory=dict)
    irreversible: bool = False

    # 1-release compat: old-style fields (removed next release)
    exit_code: int | None = None
    pipeline_status: str | None = None
    recovery: str | None = None

    def __post_init__(self) -> None:
        # Compat: old exit_code=0 / pipeline_status → new markers
        if self.exit_code == 0 and self.pipeline_status and not self.markers:
            self.markers = [self.pipeline_status]
