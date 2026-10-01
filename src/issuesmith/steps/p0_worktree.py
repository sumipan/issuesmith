"""Deprecated compat re-exports — import from issuesmith.worktree instead (#4274).

Kept so existing consumers that still import the old P0 step names keep working
until they switch to the public ``issuesmith.worktree`` API. The underscore
names keep their old signatures and defaults; the bodies delegate to
``issuesmith.worktree``.
"""

from __future__ import annotations

import warnings
from pathlib import Path

from ghdag.forge import ForgePort
from ghdag.workflow.state_machine import _load_workflow_config, transition

from issuesmith import worktree as _worktree
from issuesmith.config import get_config
from issuesmith.contract import StepResult
from issuesmith.worktree import WorktreeError, fetch_base_with_retry, validate_branch

__all__ = [
    "WorktreeError",
    "_assert_jobs_clean",
    "_ensure_base_included",
    "_github_client",
    "_handle_milestone",
    "_require_yaml_metadata",
    "fetch_base_with_retry",
    "validate_branch",
]

warnings.warn(
    "issuesmith.steps.p0_worktree is deprecated; use issuesmith.worktree instead",
    DeprecationWarning,
    stacklevel=2,
)

_MILESTONE_COMMENT = """## P0 中断: scope:milestone イシュー
このイシューは設計専用です。develop-ready による自動実装は禁止されています。
PIPELINE_STATUS: MILESTONE_BLOCKED"""

_STALE_BASE_COMMENT_TEMPLATE = """\
## P0 停止: base_branch 同期失敗（rebase 競合）

`origin/{base_branch}` との rebase で競合が発生しました。
手動で競合を解消してから再 dispatch してください。

**競合ファイル:**
```
{conflict_files}
```

PIPELINE_STATUS: STALE_BASE"""


def _transition_to_draft_done(issue_number: int) -> None:
    workflow = _load_workflow_config(get_config().paths.workflow)
    transition(
        issue_number,
        "issuesmith:draft-done",
        workflow.transitions or {},
        workflow.reset_label,
    )


def _github_client() -> ForgePort:
    return _worktree.github_client()


def _require_yaml_metadata(body: str) -> None:
    _worktree.require_yaml_metadata(body)


def _handle_milestone(client: ForgePort, issue_number: int) -> StepResult:
    return _worktree.handle_milestone(
        client,
        issue_number,
        comment=_MILESTONE_COMMENT,
        on_transition=_transition_to_draft_done,
    )


def _ensure_base_included(
    worktree_dir: Path,
    base_branch: str,
    client: ForgePort,
    issue_number: int,
) -> StepResult | None:
    return _worktree.ensure_base_included(
        worktree_dir,
        base_branch,
        client,
        issue_number,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )


def _assert_jobs_clean(worktree_dir: Path) -> None:
    _worktree.assert_jobs_clean(worktree_dir, "jobs/")
