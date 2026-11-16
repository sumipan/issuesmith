"""Publish, open or find a PR, merge, and finalize the reference develop line."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.verbs import (
    cleanup_branches,
    cleanup_worktrees,
    close_issue,
    find_pr,
    merge_pr,
    merge_state,
    publish_branch,
)


def _repo_slug(ctx: StepContext) -> str:
    return (ctx.issue_repo.strip() or get_config().repo).strip()


_BLOCKED_MERGEABLE = frozenset({"false", "conflicting", "dirty"})


def _pr_number_from_url(url: str) -> int | None:
    match = re.search(r"/pull/(\d+)(?:\s|$)", url.strip())
    if not match:
        return None
    return int(match.group(1))


def _locate_pr(
    client, repo: str, branch: str, issue: int, *, create_url: str = ""
) -> int | None:
    pr = find_pr(client, repo, branch, issue)
    if pr is not None:
        return pr
    parsed = _pr_number_from_url(create_url)
    if parsed is not None:
        return parsed
    for pull in client.pr_list(state="open", repo=repo):
        head = str(pull.get("headRefName") or "")
        title = str(pull.get("title") or "")
        number = pull.get("number")
        if not isinstance(number, int):
            continue
        if head == branch or f"#{issue}" in title:
            return number
    return None


def _git_toplevel(worktree: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git rev-parse failed")
    return Path(proc.stdout.strip())


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    _ = step
    worktree = Path(ctx.worktree_path)
    repo = _repo_slug(ctx)
    issue = int(ctx.issue_number)
    client = get_forge(repo=repo)
    branch = ctx.branch.strip()

    try:
        publish_branch(worktree, branch)
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=f"publish failed: {exc}"),
        )

    create_url = ""
    pr = _locate_pr(client, repo, branch, issue)
    if pr is None:
        title = f"Implement #{issue}"
        body = f"Closes #{issue}"
        try:
            create_url = str(
                client.pr_create(
                    base=ctx.base_branch,
                    head=branch,
                    title=title,
                    body=body,
                    repo=repo,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary=f"pr_create failed: {exc}"),
            )
        pr = _locate_pr(client, repo, branch, issue, create_url=create_url)
        if pr is None:
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary="PR not found after create"),
            )

    state = merge_state(client, repo, pr)
    mergeable = str(state.get("mergeable") or "").lower()
    if mergeable in _BLOCKED_MERGEABLE:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="blocked",
                summary=f"PR #{pr} is not mergeable: {state}",
            ),
        )

    try:
        merge_pr(client, repo, pr)
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=f"merge failed: {exc}"),
        )

    repo_root = _git_toplevel(worktree)
    cleanup_worktrees(repo_root, ctx.issue_number)
    cleanup_branches(repo_root, ctx.issue_number)
    close_issue(client, issue)
    return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)
