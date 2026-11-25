"""publish_branch verb — public extraction from ops.publish."""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from issuesmith.bump_commits import (
    BumpCommitsError,
    bump_versions_in_range,
    strip_bump_version_lines,
)
from issuesmith.ops.publish import (
    PublishResult,
    _build_pr_metadata,
    _parse_allow_paths,
    _push_branch,
    publish,
    run_version_bump,
)
from issuesmith.version_policy import bump_paths, is_bump_only_change


@dataclass(frozen=True)
class RebumpResult:
    status: Literal["rebumped", "not_bump_only", "failed"]
    conflict_files: tuple[str, ...] = ()
    detail: str = ""


def _run_git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _reset_hard(worktree: Path, ref: str) -> None:
    _run_git(worktree, "reset", "--hard", ref)


def _lines_added_vs_base(worktree: Path, base_branch: str, rel: str) -> frozenset[str]:
    path = worktree / rel
    if not path.is_file():
        return frozenset()
    head_lines = {ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
    show = _run_git(worktree, "show", f"origin/{base_branch}:{rel}")
    base_lines: set[str] = set()
    if show.returncode == 0:
        base_lines = {ln for ln in show.stdout.splitlines() if ln.strip()}
    return frozenset(head_lines - base_lines)


def _non_bump_diff_fingerprint(
    worktree: Path,
    base_branch: str,
    bump_versions: list[str],
) -> dict[str, object]:
    out: dict[str, object] = {}
    out["CHANGELOG.md"] = _lines_added_vs_base(worktree, base_branch, "CHANGELOG.md")

    diff = _run_git(worktree, "diff", f"origin/{base_branch}...HEAD").stdout
    stripped = strip_bump_version_lines(diff, bump_versions)
    try:
        current_bumps = bump_versions_in_range(worktree, base_branch)
    except BumpCommitsError:
        current_bumps = []
    stripped = strip_bump_version_lines(stripped, current_bumps)

    by_file: dict[str, Counter[str]] = {}
    current_file: str | None = None
    skip = set(bump_paths(worktree, base_branch))
    for line in stripped.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = parts[3]
                if path.startswith("b/"):
                    path = path[2:]
                current_file = None if path in skip else path
                if current_file:
                    by_file.setdefault(current_file, Counter())
            continue
        if current_file is None:
            continue
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+") or line.startswith("-"):
            by_file[current_file][line] += 1
    out.update(by_file)
    return out


def _merge_tree_conflicts(worktree: Path, base_branch: str) -> list[str] | None:
    """Return conflicting paths from merge-tree, or None on merge-tree failure."""
    proc = _run_git(
        worktree,
        "merge-tree",
        "--write-tree",
        "--name-only",
        f"origin/{base_branch}",
        "HEAD",
    )
    # Exit 0: clean merge. Exit 1: conflicts; stdout is the tree OID, then the
    # conflicted paths (one per line, --name-only), then a blank line and messages.
    # Any other exit code is a merge-tree failure.
    if proc.returncode == 0:
        return []
    if proc.returncode != 1:
        return None
    lines = (proc.stdout or "").splitlines()
    paths: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            break
        if line not in paths:
            paths.append(line)
    return paths


def rebump_onto_base(worktree: Path, base_branch: str, branch: str) -> RebumpResult:
    """Drop publish bumps, rebase onto base, re-bump, and force-push once when safe."""
    worktree = Path(worktree)

    fetched = _run_git(worktree, "fetch", "origin", base_branch)
    if fetched.returncode != 0:
        detail = (fetched.stderr or fetched.stdout or "").strip() or "git fetch failed"
        return RebumpResult(status="failed", detail=detail)

    branch_fetch = _run_git(
        worktree,
        "fetch",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
    )
    if branch_fetch.returncode != 0:
        detail = (branch_fetch.stderr or branch_fetch.stdout or "").strip()
        return RebumpResult(status="failed", detail=detail or "branch fetch failed")

    try:
        bumps = bump_versions_in_range(worktree, base_branch)
    except BumpCommitsError as exc:
        return RebumpResult(status="failed", detail=str(exc))
    if not bumps:
        return RebumpResult(
            status="not_bump_only",
            detail="no publish bump commit in range",
        )

    ancestor = _run_git(
        worktree,
        "merge-base",
        "--is-ancestor",
        f"origin/{base_branch}",
        "HEAD",
    )
    if ancestor.returncode == 0:
        return RebumpResult(
            status="not_bump_only",
            detail=f"branch already contains origin/{base_branch}",
        )

    allowed_bump_paths = bump_paths(worktree, base_branch)

    conflicts = _merge_tree_conflicts(worktree, base_branch)
    if conflicts is None:
        return RebumpResult(status="failed", detail="merge-tree failed")
    if any(p not in allowed_bump_paths for p in conflicts):
        return RebumpResult(
            status="not_bump_only",
            conflict_files=tuple(conflicts),
        )

    if not is_bump_only_change(worktree, base_branch):
        return RebumpResult(
            status="not_bump_only",
            detail="pyproject.toml changes beyond the version line",
        )

    head_before = _run_git(worktree, "rev-parse", "HEAD").stdout.strip()
    remote_branch_before = _run_git(
        worktree, "rev-parse", f"refs/remotes/origin/{branch}"
    ).stdout.strip()
    fingerprint_before = _non_bump_diff_fingerprint(worktree, base_branch, bumps)

    from issuesmith.ops.publish import ensure_rebased

    rebase_result = ensure_rebased(worktree, base_branch, [])
    if rebase_result is not None:
        _reset_hard(worktree, head_before)
        return RebumpResult(
            status="failed",
            detail=rebase_result.stderr or rebase_result.status,
        )

    bump = run_version_bump(worktree, f"origin/{base_branch}")
    if bump.returncode != 0:
        _reset_hard(worktree, head_before)
        detail = (bump.stderr or bump.stdout or "").strip() or "version bump failed"
        return RebumpResult(status="failed", detail=detail)

    fingerprint_after = _non_bump_diff_fingerprint(worktree, base_branch, bumps)
    if fingerprint_after != fingerprint_before:
        _reset_hard(worktree, head_before)
        return RebumpResult(
            status="failed",
            detail="non-bump diff changed after rebase",
        )

    push_result = _push_branch(worktree, branch)
    if push_result is not None:
        _reset_hard(worktree, head_before)
        if remote_branch_before:
            _run_git(
                worktree,
                "push",
                f"--force-with-lease={branch}:{remote_branch_before}",
                "origin",
                branch,
            )
        detail = push_result.stderr or push_result.status
        return RebumpResult(status="failed", detail=detail)

    return RebumpResult(status="rebumped")


def publish_branch(repo_cwd: Path, branch: str, remote: str = "origin") -> None:
    """Push *branch* to *remote*, using force-with-lease when already published.

    Delegates to the rebase-aware push logic in ops.publish for the default
    "origin" remote; falls back to a plain push for non-origin remotes.
    """
    repo_cwd = Path(repo_cwd)
    if remote == "origin":
        result = _push_branch(repo_cwd, branch)
        if result is not None:
            raise RuntimeError(f"push failed [{result.status}]: {result.stderr}")
        return

    proc = subprocess.run(
        ["git", "-C", str(repo_cwd), "push", "-u", remote, branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git push to {remote} failed: {detail}")


def pr_title_and_body(
    issue_number: int,
    repo: str,
    issue_repo: str,
) -> tuple[str, str]:
    """Return PR title and body using the same language-pack keys as ops publish."""
    return _build_pr_metadata(issue_number, repo, issue_repo)


def publish_pr(
    worktree: Path,
    *,
    issue_number: int,
    branch: str,
    base_branch: str,
    repo: str,
    issue_repo: str,
    allow_paths: str = "",
) -> PublishResult:
    """Push branch and open or reuse a PR via ops.publish (verb boundary)."""
    worktree = Path(worktree)
    paths = _parse_allow_paths(allow_paths if allow_paths.strip() else None)
    try:
        return publish(
            issue_number=issue_number,
            branch=branch,
            base_branch=base_branch,
            worktree=worktree,
            repo=repo,
            issue_repo=issue_repo,
            allow_paths=paths,
        )
    except subprocess.CalledProcessError as exc:
        stderr = ""
        for stream in (exc.stderr, exc.stdout, str(exc)):
            if stream and str(stream).strip():
                stderr = str(stream).strip()
                break
        if not stderr:
            stderr = "git command failed"
        exit_code = exc.returncode if exc.returncode != 0 else 1
        return PublishResult(status="GIT_FAILED", stderr=stderr, exit_code=exit_code)


__all__ = [
    "PublishResult",
    "RebumpResult",
    "pr_title_and_body",
    "publish_branch",
    "publish_pr",
    "rebump_onto_base",
]
