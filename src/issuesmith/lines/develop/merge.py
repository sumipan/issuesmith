"""Publish, open or find a PR, merge, and finalize the reference develop line."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.verbs import (
    RebumpResult,
    cleanup_branches,
    cleanup_worktrees,
    close_issue,
    find_pr,
    merge_pr,
    merge_state,
    publish_branch,
    rebump_onto_base,
)


def _repo_slug(ctx: StepContext) -> str:
    return (ctx.issue_repo.strip() or get_config().repo).strip()


def _abs_under_config(path: Path) -> Path:
    if path.is_absolute():
        return path
    return get_config().root / path


def _cross_repo(ctx: StepContext) -> bool:
    if get_config().host_is_repo_checkout:
        return ctx.is_cross_repo == "true"
    return True


def _target_clone(ctx: StepContext) -> Path:
    if ctx.target_clone_path.strip():
        return _abs_under_config(Path(ctx.target_clone_path))
    target = ctx.target_repo.strip()
    repo_tail = target.split("/")[-1] if target else ""
    if not repo_tail:
        raise RuntimeError("target_clone_path is missing for cross-repo merge cleanup")
    external = get_config().paths.external_dir
    return _abs_under_config(Path(external) / repo_tail)


_BLOCKED_MERGEABLE = frozenset({"false", "conflicting", "dirty"})


def _pr_number_from_url(url: str) -> int | None:
    match = re.search(r"/pull/(\d+)(?:\s|$)", url.strip())
    if not match:
        return None
    return int(match.group(1))


def _locate_pr(
    client,
    repo: str,
    branch: str,
    issue: int,
    *,
    create_url: str = "",
    match_issue_in_title: bool = True,
) -> int | None:
    if match_issue_in_title:
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
        if head == branch:
            return number
        if match_issue_in_title and re.search(rf"(?<!\d)#{issue}(?!\d)", title):
            return number
    return None


def _rebump_andon_summary(rebump: RebumpResult) -> str:
    parts = [f"rebump status={rebump.status}"]
    if rebump.conflict_files:
        parts.append(f"conflicts={list(rebump.conflict_files)}")
    if rebump.detail:
        parts.append(rebump.detail)
    return "; ".join(parts)


def _blocked_andon(pr: int, state: dict, extra: str = "") -> StepResult:
    summary = f"PR #{pr} is not mergeable: {state}"
    if extra:
        summary = f"{summary}; {extra}"
    return StepResult(
        status="andon",
        andon=Andon(kind="blocked", summary=summary),
    )


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
    if not worktree.is_absolute():
        worktree = get_config().root / worktree
    issue_repo = _repo_slug(ctx)
    cross = _cross_repo(ctx)
    pr_repo = ctx.target_repo.strip() if cross else issue_repo
    issue = int(ctx.issue_number)
    pr_client = get_forge(repo=pr_repo)
    issue_client = get_forge(repo=issue_repo)
    branch = ctx.branch.strip()
    repos_differ = pr_repo != issue_repo
    match_title = not repos_differ

    try:
        publish_branch(worktree, branch)
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=f"publish failed: {exc}"),
        )

    create_url = ""
    pr = _locate_pr(
        pr_client, pr_repo, branch, issue, match_issue_in_title=match_title
    )
    if pr is None:
        if repos_differ:
            title = f"Implement {issue_repo}#{issue}"
            body = f"Closes {issue_repo}#{issue}"
        else:
            title = f"Implement #{issue}"
            body = f"Closes #{issue}"
        try:
            create_url = str(
                pr_client.pr_create(
                    base=ctx.base_branch,
                    head=branch,
                    title=title,
                    body=body,
                    repo=pr_repo,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary=f"pr_create failed: {exc}"),
            )
        pr = _locate_pr(
            pr_client,
            pr_repo,
            branch,
            issue,
            create_url=create_url,
            match_issue_in_title=match_title,
        )
        if pr is None:
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary="PR not found after create"),
            )

    rebump_attempted = False

    def _attempt_rebump() -> RebumpResult | None:
        nonlocal rebump_attempted
        if rebump_attempted:
            return None
        rebump_attempted = True
        return rebump_onto_base(worktree, ctx.base_branch, branch)

    state = merge_state(pr_client, pr_repo, pr)
    mergeable = str(state.get("mergeable") or "").lower()
    if mergeable in _BLOCKED_MERGEABLE:
        rebump = _attempt_rebump()
        if rebump is None:
            return _blocked_andon(pr, state)
        if rebump.status == "rebumped":
            state = merge_state(pr_client, pr_repo, pr)
            mergeable = str(state.get("mergeable") or "").lower()
            if mergeable in _BLOCKED_MERGEABLE:
                return _blocked_andon(
                    pr, state, "rebumped once, still not mergeable"
                )
        elif rebump.status in ("not_bump_only", "failed"):
            return _blocked_andon(pr, state, _rebump_andon_summary(rebump))
        else:
            return _blocked_andon(pr, state)

    try:
        merge_pr(pr_client, pr_repo, pr)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        if ("405" in msg or "Base branch was modified" in msg) and not rebump_attempted:
            rebump = _attempt_rebump()
            if rebump is not None and rebump.status == "rebumped":
                state = merge_state(pr_client, pr_repo, pr)
                mergeable = str(state.get("mergeable") or "").lower()
                if mergeable not in _BLOCKED_MERGEABLE:
                    try:
                        merge_pr(pr_client, pr_repo, pr)
                    except Exception as retry_exc:  # noqa: BLE001
                        return StepResult(
                            status="andon",
                            andon=Andon(
                                kind="broken", summary=f"merge failed: {retry_exc}"
                            ),
                        )
                else:
                    return _blocked_andon(
                        pr, state, "rebumped once, still not mergeable"
                    )
            elif rebump is not None and rebump.status in ("not_bump_only", "failed"):
                state = merge_state(pr_client, pr_repo, pr)
                return _blocked_andon(pr, state, _rebump_andon_summary(rebump))
            else:
                state = merge_state(pr_client, pr_repo, pr)
                return _blocked_andon(pr, state)
        else:
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary=f"merge failed: {exc}"),
            )

    if cross:
        clone = _target_clone(ctx)
        cleanup_worktrees(
            get_config().root,
            ctx.issue_number,
            is_cross_repo=True,
            target_clone_path=clone,
        )
    else:
        repo_root = _git_toplevel(worktree)
        cleanup_worktrees(repo_root, ctx.issue_number)
        cleanup_branches(repo_root, ctx.issue_number)
    close_issue(issue_client, issue)
    return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)
