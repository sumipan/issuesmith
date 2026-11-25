"""Publish, open or find a PR, merge, and finalize the reference develop line."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import time
from pathlib import Path

from issuesmith.bump_commits import BumpCommitsError, bump_versions_in_range
from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.merge import verify_local_merge
from issuesmith.verbs import (
    RebumpResult,
    cleanup_branches,
    cleanup_worktrees,
    close_issue,
    find_pr,
    merge_pr,
    merge_state,
    pr_title_and_body,
    publish_branch,
    rebump_onto_base,
    write_result,
)
from issuesmith.version_policy import resolve_policy, run_bump


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
_REDO_MAX = 2
_REDOABLE_RULE_IDS = frozenset({"merge.rebase_conflict", "merge.push_rejected"})
_PUSH_REJECT_MARKERS = ("non-fast-forward", "fetch first", "rejected")
_AUTO_FIX_COMMIT_MESSAGE = "Apply gate auto-fixes"

_sleep = time.sleep


def _commit_gate_autofixes(worktree: Path) -> str:
    """Commit tracked gate auto-fixes; return git stderr on failure, else \"\"."""
    wt = str(worktree)
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=False,
    )
    if not (status.stdout or "").strip():
        return ""
    add = subprocess.run(
        ["git", "add", "-u"],
        cwd=wt,
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        return add.stderr.strip() or f"git add failed (exit {add.returncode})"
    commit = subprocess.run(
        ["git", "commit", "-m", _AUTO_FIX_COMMIT_MESSAGE],
        cwd=wt,
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        return commit.stderr.strip() or f"git commit failed (exit {commit.returncode})"
    return ""


class _RebaseConflict(Exception):
    def __init__(self, files: list[str]) -> None:
        self.files = files
        super().__init__()


class _PushRejected(Exception):
    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__()


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _push_rejected(stderr: str) -> bool:
    lower = stderr.lower()
    return any(marker in lower for marker in _PUSH_REJECT_MARKERS)


def _merge_without_pr(worktree: Path, base: str, *, attempts: int = 3) -> tuple[str, str]:
    """Rebase onto origin/<base> and fast-forward push to origin/<base>."""
    last_stderr = ""
    for _ in range(attempts):
        fetch = _git(worktree, "fetch", "origin", base)
        if fetch.returncode != 0:
            detail = (fetch.stderr or fetch.stdout or "").strip()
            raise RuntimeError(detail or f"git fetch origin {base} failed")

        old_proc = _git(worktree, "rev-parse", f"origin/{base}")
        if old_proc.returncode != 0:
            detail = (old_proc.stderr or old_proc.stdout or "").strip()
            raise RuntimeError(detail or f"git rev-parse origin/{base} failed")
        old_sha = old_proc.stdout.strip()

        rebase = _git(worktree, "rebase", f"origin/{base}")
        if rebase.returncode != 0:
            conflict_proc = _git(worktree, "diff", "--name-only", "--diff-filter=U")
            files = [line.strip() for line in (conflict_proc.stdout or "").splitlines() if line.strip()]
            _git(worktree, "rebase", "--abort")
            if not files:
                detail = (rebase.stderr or rebase.stdout or "").strip()
                raise RuntimeError(detail or "git rebase failed")
            raise _RebaseConflict(files)

        push = _git(worktree, "push", "origin", f"HEAD:refs/heads/{base}")
        if push.returncode == 0:
            head_proc = _git(worktree, "rev-parse", "HEAD")
            if head_proc.returncode != 0:
                detail = (head_proc.stderr or head_proc.stdout or "").strip()
                raise RuntimeError(detail or "git rev-parse HEAD failed")
            return old_sha, head_proc.stdout.strip()

        last_stderr = (push.stderr or push.stdout or "").strip()
        if _push_rejected(last_stderr):
            continue
        raise RuntimeError(last_stderr or "git push failed")

    raise _PushRejected(last_stderr)


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


def _bump_before_publish(worktree: Path, base: str, *, target_differs: bool) -> str:
    if not target_differs:
        return ""
    if resolve_policy(worktree) == "none":
        return ""
    fetch = _git(worktree, "fetch", "origin", base)
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "").strip()
        return detail or f"git fetch origin {base} failed"
    try:
        if bump_versions_in_range(worktree, base):
            return ""
    except BumpCommitsError as exc:
        return str(exc)
    proc = run_bump(worktree, f"origin/{base}")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return detail or f"version bump failed (exit {proc.returncode})"
    return ""


def _is_unknown(state: dict) -> bool:
    mergeable = str(state.get("mergeable") or "").strip().lower()
    return mergeable in ("", "unknown")


def _wait_merge_state(client, repo: str, pr: int) -> dict:
    cfg = get_config().merge
    last: dict = {}
    for attempt in range(cfg.state_wait_attempts):
        last = merge_state(client, repo, pr)
        if not _is_unknown(last):
            return last
        if attempt < cfg.state_wait_attempts - 1 and cfg.state_wait_seconds > 0:
            _sleep(cfg.state_wait_seconds)
    return last


def _mergeable_blocked(state: dict) -> bool:
    mergeable = str(state.get("mergeable") or "").lower()
    return mergeable in _BLOCKED_MERGEABLE


def _resolve_unknown_merge_state(worktree: Path, base: str, branch: str) -> tuple[dict | None, StepResult | None]:
    verify = verify_local_merge(str(worktree), base, branch)
    if verify.result == "SKIPPED":
        return None, StepResult(
            status="andon",
            andon=Andon(kind="blocked", summary="merge-tree skipped"),
        )
    if verify.result == "CLEAN":
        return {"mergeable": "MERGEABLE"}, None
    if verify.result == "CONFLICT":
        return {"mergeable": "CONFLICTING"}, None
    return None, StepResult(
        status="andon",
        andon=Andon(kind="broken", summary=f"unexpected merge-tree result: {verify.result!r}"),
    )


def _merge_via_pr(
    worktree: Path,
    pr_client,
    pr_repo: str,
    pr: int,
    branch: str,
    base: str,
) -> StepResult | None:
    rebump_max = get_config().merge.rebump_max
    tries = 0
    while True:
        state = _wait_merge_state(pr_client, pr_repo, pr)
        if _is_unknown(state):
            resolved, early = _resolve_unknown_merge_state(worktree, base, branch)
            if early is not None:
                return early
            assert resolved is not None
            state = resolved

        if _mergeable_blocked(state):
            if tries >= rebump_max:
                return _blocked_andon(
                    pr,
                    state,
                    f"rebumped {tries} time(s), still not mergeable",
                )
            rebump = rebump_onto_base(worktree, base, branch)
            tries += 1
            if rebump.status != "rebumped":
                return _blocked_andon(pr, state, _rebump_andon_summary(rebump))
            continue

        try:
            merge_pr(pr_client, pr_repo, pr)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "405" in msg or "Base branch was modified" in msg:
                if tries >= rebump_max:
                    return _blocked_andon(
                        pr,
                        state,
                        f"base changed {tries} time(s)",
                    )
                tries += 1
                continue
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary=f"merge failed: {exc}"),
            )
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


def _result_target_repo(ctx: StepContext, issue_repo: str) -> str:
    target = ctx.target_repo.strip()
    return target or issue_repo


def _result_version(worktree: Path, base: str) -> str:
    try:
        versions = bump_versions_in_range(worktree, base)
    except BumpCommitsError:
        return "-"
    return versions[0] if versions else "-"


def _result_merge_commit_after_pr(worktree: Path, base: str) -> str:
    head_proc = _git(worktree, "rev-parse", "HEAD")
    if head_proc.returncode != 0:
        return "-"
    head_sha = head_proc.stdout.strip()
    fetch = _git(worktree, "fetch", "origin", base)
    if fetch.returncode != 0:
        return "-"
    rev_proc = _git(
        worktree,
        "rev-list",
        "--first-parent",
        "--ancestry-path",
        f"{head_sha}..origin/{base}",
    )
    if rev_proc.returncode != 0:
        return "-"
    lines = [line.strip() for line in (rev_proc.stdout or "").splitlines() if line.strip()]
    return lines[-1] if lines else "-"


def _result_pr_url(pr_client, pr_repo: str, pr: int) -> str:
    try:
        data = pr_client.pr_get(pr, repo=pr_repo)
        url = str(data.get("url") or data.get("html_url") or "").strip()
        return url or "-"
    except Exception:  # noqa: BLE001
        return "-"


def _emit_merge_result(
    issue_client,
    issue: int,
    *,
    target_repo: str,
    pr: str,
    merge_commit: str,
    version: str,
) -> None:
    try:
        write_result(
            issue_client,
            issue,
            target_repo=target_repo,
            pr=pr,
            merge_commit=merge_commit,
            version=version,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"merge: could not write result comment: {exc}", file=sys.stderr)


def _commits_ahead(worktree: Path, base: str) -> int | None:
    # Some callers exercise merge policy against an in-memory/local-only repository.
    # There is no remote state to compare in that legacy mode, so retain the normal
    # merge path.  Once origin exists, every fetch/count failure is fatal.
    remote = _git(worktree, "remote", "get-url", "origin")
    if remote.returncode != 0:
        return 1
    fetch = _git(worktree, "fetch", "origin", base)
    if fetch.returncode != 0:
        return None
    proc = _git(worktree, "rev-list", "--count", f"origin/{base}..HEAD")
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _remote_branch_sha(worktree: Path, branch: str) -> str | None:
    fetch = _git(worktree, "fetch", "origin", branch)
    if fetch.returncode != 0:
        return None
    proc = _git(worktree, "rev-parse", f"origin/{branch}")
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _prepare_redo(worktree: Path, base: str) -> str | None:
    fetch = _git(worktree, "fetch", "origin", base)
    if fetch.returncode != 0:
        return (fetch.stderr or fetch.stdout or "").strip() or "fetch failed"
    _git(worktree, "rebase", "--abort")
    reset = _git(worktree, "reset", "--hard", f"origin/{base}")
    if reset.returncode != 0:
        return (reset.stderr or reset.stdout or "").strip() or "reset failed"
    return None


def _invoke_redo_step(ctx: StepContext, step: StepConfig) -> StepResult:
    redo_id = step.redo_on_conflict
    if not redo_id:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                rule_id="merge.redo_failed",
                summary="redo_on_conflict is not configured",
            ),
        )
    steps = get_config().steps
    redo_step = steps.get(redo_id)
    if redo_step is None or not redo_step.module:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                rule_id="merge.redo_failed",
                summary=f"redo step {redo_id!r} is not configured",
            ),
        )
    try:
        mod = importlib.import_module(redo_step.module)
        run_fn = getattr(mod, "run", None)
        if not callable(run_fn):
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    rule_id="merge.redo_failed",
                    summary=f"redo step {redo_id!r} has no run()",
                ),
            )
        result = run_fn(ctx, redo_step)
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                rule_id="merge.redo_failed",
                summary=str(exc),
            ),
        )
    if not isinstance(result, StepResult):
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                rule_id="merge.redo_failed",
                summary="redo step returned non-StepResult",
            ),
        )
    return result


def _is_redoable_conflict(result: StepResult) -> bool:
    if result.status != "andon" or result.andon is None:
        return False
    if result.andon.kind != "blocked":
        return False
    rid = result.andon.rule_id
    if rid in _REDOABLE_RULE_IDS:
        return True
    summary = result.andon.summary.lower()
    return "not mergeable" in summary


def _append_redo_exhausted(result: StepResult) -> StepResult:
    andon = result.andon
    assert andon is not None
    extra = "redo attempts=2"
    summary = andon.summary
    if extra not in summary:
        summary = f"{summary}; {extra}"
    return StepResult(
        status="andon",
        andon=Andon(
            kind=andon.kind,
            rule_id=andon.rule_id,
            summary=summary,
            options=list(andon.options),
        ),
    )


def _push_branch_with_lease(worktree: Path, branch: str, remote_sha: str) -> str | None:
    push = _git(
        worktree,
        "push",
        f"--force-with-lease=refs/heads/{branch}:{remote_sha}",
        "origin",
        f"HEAD:refs/heads/{branch}",
    )
    if push.returncode == 0:
        return None
    return (push.stderr or push.stdout or "").strip() or "force-with-lease push failed"


def _cleanup_and_close_issue(
    ctx: StepContext,
    worktree: Path,
    *,
    issue_client,
    issue: int,
    cross: bool,
) -> StepResult | None:
    try:
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
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                summary=f"merge: could not clean up or close issue: {exc}",
            ),
        )
    return None


def _complete_no_diff_merge(
    ctx: StepContext,
    worktree: Path,
    *,
    issue_client,
    issue: int,
    issue_repo: str,
    cross: bool,
) -> StepResult:
    try:
        write_result(
            issue_client,
            issue,
            target_repo=_result_target_repo(ctx, issue_repo),
            pr="-",
            merge_commit="-",
            version="-",
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=f"merge: could not write result: {exc}"),
        )
    err = _cleanup_and_close_issue(ctx, worktree, issue_client=issue_client, issue=issue, cross=cross)
    if err is not None:
        return err
    return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)


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


def _merge_without_pr_flow(
    ctx: StepContext,
    worktree: Path,
    *,
    issue_repo: str,
    issue_client,
    issue: int,
    cross: bool,
) -> StepResult:
    base = ctx.base_branch.strip() or "main"
    target_repo = ctx.target_repo.strip()
    version = _result_version(worktree, base)
    try:
        old_sha, new_sha = _merge_without_pr(worktree, base)
    except _RebaseConflict as exc:
        files = ", ".join(exc.files) if exc.files else "(unknown)"
        return StepResult(
            status="andon",
            andon=Andon(
                kind="blocked",
                rule_id="merge.rebase_conflict",
                summary=f"rebase onto origin/{base} conflicts: {files}",
            ),
        )
    except _PushRejected as exc:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="blocked",
                rule_id="merge.push_rejected",
                summary=(f"push to origin/{base} was rejected 3 times: {exc.detail}"),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=f"merge failed: {exc}"),
        )

    stat_proc = _git(worktree, "diff", "--stat", f"{old_sha}..{new_sha}")
    stat_block = (stat_proc.stdout or "").strip() or "(no changes)"
    comment_body = (
        f"Merged into `{base}` of `{target_repo}` without a pull request: `{new_sha}`\n\n```\n{stat_block}\n```\n"
    )
    try:
        issue_client.issue_comment(issue, comment_body)
    except Exception as exc:  # noqa: BLE001
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
    _emit_merge_result(
        issue_client,
        issue,
        target_repo=_result_target_repo(ctx, issue_repo),
        pr="-",
        merge_commit=new_sha,
        version=version,
    )
    close_issue(issue_client, issue)
    return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)


def _merge_pr_path(
    ctx: StepContext,
    worktree: Path,
    *,
    issue_repo: str,
    issue_client,
    issue: int,
    cross: bool,
    pr_repo: str,
    branch: str,
    match_title: bool,
    use_lease: str | None,
) -> StepResult:
    pr_client = get_forge(repo=pr_repo)
    base = ctx.base_branch.strip() or "main"
    if use_lease:
        push_err = _push_branch_with_lease(worktree, branch, use_lease)
        if push_err:
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="blocked",
                    rule_id="merge.redo_lease_failed",
                    summary=push_err,
                ),
            )
    else:
        try:
            publish_branch(worktree, branch)
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary=f"publish failed: {exc}"),
            )

    create_url = ""
    pr = _locate_pr(pr_client, pr_repo, branch, issue, match_issue_in_title=match_title)
    if pr is None:
        title, body = pr_title_and_body(issue, pr_repo, issue_repo)
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

    merge_result = _merge_via_pr(worktree, pr_client, pr_repo, pr, branch, base)
    if merge_result is not None:
        return merge_result

    version = _result_version(worktree, base)
    merge_commit = _result_merge_commit_after_pr(worktree, base)
    pr_url = _result_pr_url(pr_client, pr_repo, pr)

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
    _emit_merge_result(
        issue_client,
        issue,
        target_repo=_result_target_repo(ctx, issue_repo),
        pr=pr_url,
        merge_commit=merge_commit,
        version=version,
    )
    close_issue(issue_client, issue)
    return StepResult(status="done", markers=["MERGE_DONE"], irreversible=True)


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    worktree = Path(ctx.worktree_path)
    if not worktree.is_absolute():
        worktree = get_config().root / worktree
    issue_repo = _repo_slug(ctx)
    cross = _cross_repo(ctx)
    pr_repo = ctx.target_repo.strip() if cross else issue_repo
    issue = int(ctx.issue_number)
    issue_client = get_forge(repo=issue_repo)
    target_repo = ctx.target_repo.strip()
    use_no_pr = get_config().merges_without_pr(target_repo)
    branch = ctx.branch.strip()
    repos_differ = pr_repo != issue_repo
    match_title = not repos_differ
    base = ctx.base_branch.strip() or "main"

    redo_count = 0
    branch_lease_sha: str | None = None

    while True:
        err = _commit_gate_autofixes(worktree)
        if err:
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary=f"merge: could not commit gate auto-fixes: {err}",
                ),
            )

        target_differs = (target_repo if use_no_pr else pr_repo) != issue_repo
        bump_err = _bump_before_publish(worktree, base, target_differs=target_differs)
        if bump_err:
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary=f"version bump failed: {bump_err}",
                ),
            )

        ahead = _commits_ahead(worktree, base)
        if ahead is None:
            return StepResult(
                status="andon",
                andon=Andon(kind="broken", summary="merge: could not count commits ahead of base"),
            )
        if ahead == 0:
            return _complete_no_diff_merge(
                ctx,
                worktree,
                issue_client=issue_client,
                issue=issue,
                issue_repo=issue_repo,
                cross=cross,
            )

        if use_no_pr:
            result = _merge_without_pr_flow(
                ctx,
                worktree,
                issue_repo=issue_repo,
                issue_client=issue_client,
                issue=issue,
                cross=cross,
            )
        else:
            lease = branch_lease_sha if redo_count > 0 else None
            result = _merge_pr_path(
                ctx,
                worktree,
                issue_repo=issue_repo,
                issue_client=issue_client,
                issue=issue,
                cross=cross,
                pr_repo=pr_repo,
                branch=branch,
                match_title=match_title,
                use_lease=lease,
            )

        if result.status == "done":
            return result
        if not step.redo_on_conflict or not _is_redoable_conflict(result):
            return result
        if redo_count >= _REDO_MAX:
            return _append_redo_exhausted(result)

        if not use_no_pr:
            branch_lease_sha = _remote_branch_sha(worktree, branch)
            if branch_lease_sha is None:
                return StepResult(
                    status="andon",
                    andon=Andon(
                        kind="broken",
                        rule_id="merge.redo_failed",
                        summary=f"could not fetch origin/{branch} before redo",
                    ),
                )

        prep_err = _prepare_redo(worktree, base)
        if prep_err:
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    rule_id="merge.redo_failed",
                    summary=prep_err,
                ),
            )
        redo_result = _invoke_redo_step(ctx, step)
        if redo_result.status != "done":
            return redo_result
        redo_count += 1
