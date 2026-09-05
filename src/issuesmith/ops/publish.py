#!/usr/bin/env python3
"""Deterministic publish helper for issuesmith P3 (#2742)."""

from __future__ import annotations

import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

from ghdag.github_client import GitHubClient

RUNTIME_LOG_EXCLUDES: tuple[str, ...] = (
    "jobs/audit.jsonl",
    "jobs/exec.jsonl",
    "jobs/quota-gate.json",
    "jobs/loops/*/audit.jsonl",
)

RUNTIME_DIR_EXCLUDES: tuple[str, ...] = (
    "jobs/**",
    "chat/**",
    "sessions/**",
    "logs/**",
)

_UNRESTRICTED_ALLOW_PATHS = "（制限なし）"


class PublishResult(NamedTuple):
    status: str
    pr_url: str = ""
    stderr: str = ""
    exit_code: int = 0


def _run_git(worktree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _parse_porcelain_path(line: str) -> str:
    path = line[3:].strip()
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    return path.strip('"')


def _parse_allow_paths(raw: str | None) -> list[str] | None:
    """Parse context_hook format (`- path\\n- path`) into a list.

    Empty / ``（制限なし）`` / None → None (no allow_paths filtering).
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text or text == _UNRESTRICTED_ALLOW_PATHS:
        return None
    paths: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("- "):
            paths.append(line[2:].strip())
        elif line.startswith("-"):
            paths.append(line[1:].strip())
        else:
            paths.append(line)
    return paths or None


def _is_runtime_log(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in RUNTIME_LOG_EXCLUDES)


def _is_runtime_dir_excluded(path: str, allow_paths: list[str]) -> bool:
    """True if path matches RUNTIME_DIR_EXCLUDES and that pattern is not explicitly allowed."""
    for pattern in RUNTIME_DIR_EXCLUDES:
        if fnmatch.fnmatch(path, pattern):
            return pattern not in allow_paths
    return False


def _matches_allow_paths(path: str, allow_paths: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in allow_paths)


def _dirty_paths(worktree: Path) -> list[str]:
    out = _run_git(worktree, "status", "--porcelain").stdout
    return [_parse_porcelain_path(line) for line in out.splitlines() if line.strip()]


def _report_excluded(excluded: list[str]) -> None:
    if not excluded:
        return
    print("excluded from commit:", file=sys.stderr)
    for path in excluded:
        print(f"  {path}", file=sys.stderr)


def _select_commit_candidates(
    dirty: list[str],
    allow_paths: list[str],
) -> tuple[list[str], list[str]]:
    """Return (candidates to add, excluded paths)."""
    candidates: list[str] = []
    excluded: list[str] = []
    for path in dirty:
        if not _matches_allow_paths(path, allow_paths):
            excluded.append(path)
            continue
        if _is_runtime_dir_excluded(path, allow_paths):
            excluded.append(path)
            continue
        if _is_runtime_log(path):
            excluded.append(path)
            continue
        candidates.append(path)
    return candidates, excluded


def _commit_if_needed(
    worktree: Path,
    issue_number: int,
    allow_paths: list[str] | None = None,
) -> None:
    dirty = _dirty_paths(worktree)
    if not dirty:
        return

    # 後方互換: allow_paths 未指定時は従来の git add -A + RUNTIME_LOG_EXCLUDES 除外
    if allow_paths is None:
        if not any(not _is_runtime_log(p) for p in dirty):
            return
        _run_git(worktree, "add", "-A")
        staged = _run_git(worktree, "diff", "--cached", "--name-only").stdout.splitlines()
        exclude_staged = [path for path in staged if _is_runtime_log(path)]
        if exclude_staged:
            _report_excluded(exclude_staged)
            _run_git(worktree, "reset", "HEAD", "--", *exclude_staged)
        _run_git(worktree, "commit", "-m", f"実装: Issue #{issue_number}")
        return

    candidates, excluded = _select_commit_candidates(dirty, allow_paths)
    _report_excluded(excluded)
    if not candidates:
        return
    _run_git(worktree, "add", "--", *candidates)
    _run_git(worktree, "commit", "-m", f"実装: Issue #{issue_number}")


def _ahead_commit_count(worktree: Path, base_branch: str) -> int:
    out = _run_git(worktree, "rev-list", "--count", f"origin/{base_branch}..HEAD").stdout.strip()
    return int(out or "0")


def _build_pr_metadata(
    issue_number: int,
    repo: str,
    issue_repo: str,
    target_count: int = 1,
) -> tuple[str, str]:
    # cross-repo（repo != issue_repo）の PR は、target_count に関わらず絶対に Closes を
    # 使わない。理由: cross-repo issue は必ず M2 finalize（issue_repo 側での
    # merge-done 遷移 + close）を経る設計であり、target repo 側 PR のマージだけで
    # issue を閉じてしまうと finalize が一生走らないまま孤立する
    # （2026-09-06 実測: da499bf で ref を qualified 化した副作用として、それまで
    # 未修飾で機能していなかった cross-repo Closes が実際に発火するようになり、
    # #2873 が M1 完走前に premature close された）。
    if repo != issue_repo:
        ref = f"{issue_repo}#{issue_number}"
        return (
            f"実装: {issue_repo}#{issue_number}",
            f"P1/P2 result より自動生成。\n\nRefs {ref}",
        )
    if target_count > 1:
        return (
            f"実装: Issue #{issue_number}",
            f"P1/P2 result より自動生成。\n\nRefs #{issue_number}",
        )
    return (
        f"実装: Issue #{issue_number}",
        f"P1/P2 result より自動生成。\n\nCloses #{issue_number}",
    )


def _run_version_bump(worktree: Path, base_branch: str) -> subprocess.CompletedProcess[str]:
    # Prefer the scripts/ shim so frozen orders and subprocess callers keep working.
    from issuesmith.config import get_config

    script = get_config().root / "scripts" / "issuesmith-version-bump.py"
    return subprocess.run(
        [sys.executable, str(script), "--worktree", str(worktree), "--base", base_branch],
        capture_output=True,
        text=True,
    )


def _maybe_bump_version(
    worktree: Path,
    base_branch: str,
    repo: str,
    issue_repo: str,
) -> PublishResult | None:
    """cross-repo かつ pyproject.toml があるときだけ決定論バンプを実行する.

    失敗時は PublishResult(status="BUMP_FAILED") を返す。成功・スキップ時は None。
    """
    if repo == issue_repo:
        return None
    if not (worktree / "pyproject.toml").is_file():
        return None

    result = _run_version_bump(worktree, base_branch)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"version bump exited {result.returncode}"
        return PublishResult(status="BUMP_FAILED", stderr=err, exit_code=1)
    return None


def publish(
    *,
    issue_number: int,
    branch: str,
    base_branch: str,
    worktree: Path,
    repo: str,
    issue_repo: str,
    allow_paths: list[str] | None = None,
    target_count: int = 1,
) -> PublishResult:
    _commit_if_needed(worktree, issue_number, allow_paths)

    bump_fail = _maybe_bump_version(worktree, base_branch, repo, issue_repo)
    if bump_fail is not None:
        return bump_fail

    if _ahead_commit_count(worktree, base_branch) == 0:
        return PublishResult(status="NO_DIFF", exit_code=1)

    _run_git(worktree, "push", "-u", "origin", branch)

    client = GitHubClient(repo=repo)
    existing = client.pr_list(head=branch, state="all", limit=1)
    if existing:
        return PublishResult(status="OK", pr_url=existing[0].get("url", ""), exit_code=0)

    title, body = _build_pr_metadata(issue_number, repo, issue_repo, target_count=target_count)
    try:
        pr_url = client.pr_create(base=base_branch, head=branch, title=title, body=body)
    except Exception as exc:  # noqa: BLE001
        return PublishResult(status="PR_CREATE_FAILED", stderr=str(exc), exit_code=1)

    return PublishResult(status="OK", pr_url=pr_url, exit_code=0)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue-repo", required=True)
    parser.add_argument(
        "--allow-paths",
        default=None,
        help='context_hook format: "- path1\\n- path2". Omit or "（制限なし）" for no filter.',
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=1,
        help="Issue のターゲット数。2 以上のとき PR 本文は Refs を使用する",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    result = publish(
        issue_number=args.issue,
        branch=args.branch,
        base_branch=args.base,
        worktree=Path(args.worktree),
        repo=args.repo,
        issue_repo=args.issue_repo,
        allow_paths=_parse_allow_paths(args.allow_paths),
        target_count=args.target_count,
    )

    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.pr_url:
        print(f"PR_URL: {result.pr_url}")
    print(f"PUBLISH_STATUS: {result.status}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
