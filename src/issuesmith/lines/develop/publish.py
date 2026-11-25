"""Develop line publish step: push branch and open or reuse a PR."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.verbs import PublishResult, publish_pr

_AUTO_FIX_COMMIT_MESSAGE = "Apply gate auto-fixes"

_PUBLISH_FAILURE_MAP: dict[str, tuple[Literal["blocked", "broken"], str]] = {
    "REBASE_CONFLICT": ("blocked", "publish.rebase_conflict"),
    "PUSH_DIVERGED": ("blocked", "publish.push_diverged"),
    "NO_DIFF": ("blocked", "publish.no_diff"),
    "DIRTY_WORKTREE": ("broken", "publish.dirty_worktree"),
    "FETCH_FAILED": ("broken", "publish.fetch_failed"),
    "P3_GATE_FAILED": ("broken", "publish.diff_gate_failed"),
    "BUMP_FAILED": ("broken", "publish.bump_failed"),
    "PUSH_FAILED": ("broken", "publish.push_failed"),
    "PR_LIST_FAILED": ("broken", "publish.pr_list_failed"),
    "PR_CREATE_FAILED": ("broken", "publish.pr_create_failed"),
    "GIT_FAILED": ("broken", "publish.git_failed"),
}


def _repo_slug(ctx: StepContext) -> str:
    return (ctx.issue_repo.strip() or get_config().repo).strip()


def _cross_repo(ctx: StepContext) -> bool:
    if get_config().host_is_repo_checkout:
        return ctx.is_cross_repo == "true"
    return True


def _worktree_path(ctx: StepContext) -> Path:
    raw = ctx.worktree_path.strip()
    path = Path(raw)
    if path.is_absolute():
        return path
    return get_config().root / path


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _commit_gate_autofixes(worktree: Path) -> bool:
    """Commit tracked gate auto-fixes; return False on git failure."""
    status = _git(worktree, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        return False
    if not (status.stdout or "").strip():
        return True
    add = _git(worktree, "add", "-u")
    if add.returncode != 0:
        return False
    commit = _git(worktree, "commit", "-m", _AUTO_FIX_COMMIT_MESSAGE)
    return commit.returncode == 0


def _failure_detail(result: PublishResult) -> str:
    raw = (result.stderr or "").strip()
    if not raw:
        return "(no detail)"
    if len(raw) > 1000:
        return raw[-1000:]
    return raw


def _failure_summary(status: str, detail: str) -> str:
    return get_config().language.message("publish.step_failed", status=status, detail=detail)


def _andon_from_publish(result: PublishResult) -> StepResult:
    status = result.status
    detail = _failure_detail(result)
    summary = _failure_summary(status, detail)
    if status == "OK" and not (result.pr_url or "").strip():
        kind: Literal["blocked", "broken"] = "broken"
        rule_id = "publish.pr_missing"
    elif status in _PUBLISH_FAILURE_MAP:
        kind, rule_id = _PUBLISH_FAILURE_MAP[status]
    else:
        kind, rule_id = "broken", "publish.unknown_status"
    return StepResult(
        status="andon",
        andon=Andon(kind=kind, rule_id=rule_id, summary=summary),
    )


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    _ = step
    worktree = _worktree_path(ctx)
    issue_repo = _repo_slug(ctx)
    cross = _cross_repo(ctx)
    pr_repo = ctx.target_repo.strip() if cross else issue_repo
    target_repo = ctx.target_repo.strip()

    if get_config().merges_without_pr(target_repo):
        return StepResult(status="done", markers=["PUBLISH_DONE"])

    if not _commit_gate_autofixes(worktree):
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                rule_id="publish.autofix_commit_failed",
                summary=_failure_summary("autofix", "(no detail)"),
            ),
        )

    result = publish_pr(
        worktree,
        issue_number=int(ctx.issue_number),
        branch=ctx.branch.strip(),
        base_branch=ctx.base_branch.strip() or "main",
        repo=pr_repo,
        issue_repo=issue_repo,
        allow_paths=ctx.allow_paths,
    )

    if result.status != "OK" or not (result.pr_url or "").strip():
        return _andon_from_publish(result)

    print(f"PR_URL: {result.pr_url}", file=sys.stdout)
    return StepResult(status="done", markers=["PUBLISH_DONE"])
