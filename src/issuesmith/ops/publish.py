#!/usr/bin/env python3
"""Deterministic publish helper for issuesmith P3 (#2742)."""

from __future__ import annotations

import argparse
import fnmatch
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from ghdag.forge import get_forge

from issuesmith.gate_rules.cp1 import (
    check_test_version_exact_assert,
    check_version_line_in_diff,
)

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
    # GitHub の "Closes #N" auto-close は一切使わない（target_count・same-repo/cross-repo
    # を問わない）。issue を閉じるのは常に M2 finalize（issue_repo 側での merge-done
    # 遷移 + issue_close()）の役目であり、GitHub の自動 close はそれより早く発火しうる
    # 副作用の強い機構だった。実測: #2852（diary companion 側）・#2873（da499bf で
    # cross-repo の qualified Closes が実際に発火するようになった回帰）と、同じ
    # クラスの premature close 事故が 2 回起きている。"Refs #N" は PR 本文の検索
    # マーカーとして queue.py 側で引き続き使う（_closes_issue_marker 参照）。
    if repo != issue_repo:
        return (
            f"実装: {issue_repo}#{issue_number}",
            f"P1/P2 result より自動生成。\n\nRefs {issue_repo}#{issue_number}",
        )
    return (
        f"実装: Issue #{issue_number}",
        f"P1/P2 result より自動生成。\n\nRefs #{issue_number}",
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


_BUMP_SUBJECT_PREFIX = "chore: bump version to "


def _bump_commits_on_top(worktree: Path, base_branch: str) -> int:
    """Number of consecutive publish-made bump commits at the top of ``origin/<base>..HEAD``.

    ``publish`` commits the bump, pushes, then looks up / creates the PR. When a later step
    fails (rate limit on ``pr_list``, PR creation, M1) and P3 is re-run, HEAD already carries
    the bump commit. Without this the diff gate rejects our own bump and a second run would
    bump again (#3767 / #3794).
    """
    log = _run_git(
        worktree, "log", "--format=%s", f"origin/{base_branch}..HEAD", check=False
    ).stdout.splitlines()
    count = 0
    for subject in log:
        if subject.startswith(_BUMP_SUBJECT_PREFIX):
            count += 1
        else:
            break
    return count


def _maybe_bump_version(
    worktree: Path,
    base_branch: str,
    repo: str,
    issue_repo: str,
) -> PublishResult | None:
    """cross-repo かつ pyproject.toml があるときだけ決定論バンプを実行する.

    失敗時は PublishResult(status="BUMP_FAILED") を返す。成功・スキップ時は None。
    既に HEAD が publish の bump commit なら再バンプしない（再実行の冪等性、#3794）。
    """
    if repo == issue_repo:
        return None
    if not (worktree / "pyproject.toml").is_file():
        return None
    if _bump_commits_on_top(worktree, base_branch) > 0:
        print("version bump skipped: HEAD is already a bump commit")
        return None

    result = _run_version_bump(worktree, base_branch)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"version bump exited {result.returncode}"
        return PublishResult(status="BUMP_FAILED", stderr=err, exit_code=1)
    return None


def _check_commit_diff_gates(worktree: Path, base_branch: str) -> PublishResult | None:
    """commit 後・bump 前に version 行 / テスト完全一致 assert を検査する (#3065).

    三点ドット差分（merge-base 起点）を使う。二点ドットだと base が進んだだけで
    逆方向の version 差分が写り、偽陽性になる（#3221）。
    """
    # Our own bump commit(s) on top are not the LLM's doing: inspect the diff below them.
    skip = _bump_commits_on_top(worktree, base_branch)
    head = "HEAD" if skip == 0 else f"HEAD~{skip}"
    diff = _run_git(worktree, "diff", f"origin/{base_branch}...{head}").stdout
    violations = check_version_line_in_diff(diff) + check_test_version_exact_assert(diff)
    if not violations:
        return None
    msgs = "\n".join(f"[{v.rule_id}] {v.fix_hint or v.message}" for v in violations)
    return PublishResult(status="P3_GATE_FAILED", stderr=msgs, exit_code=1)


def _discard_runtime_dir_dirt(worktree: Path, allow_paths: list[str]) -> None:
    """Drop unstaged RUNTIME_DIR_EXCLUDES dirt so rebase can proceed (#3227).

    File-level only (not directory-wide clean/restore) to avoid sweeping
    unintended paths. Untracked (``??``) → ``git clean -f``; tracked →
    ``git restore``. ``--autostash`` is intentionally not used.
    """
    status_out = _run_git(worktree, "status", "--porcelain").stdout
    for line in status_out.splitlines():
        if not line.strip():
            continue
        path = _parse_porcelain_path(line)
        if not _is_runtime_dir_excluded(path, allow_paths):
            continue
        if line.startswith("??"):
            _run_git(worktree, "clean", "-f", "--", path)
        else:
            _run_git(worktree, "restore", "--", path)


_CHANGELOG = "CHANGELOG.md"
_CONFLICT_MARKER_PREFIXES = ("<<<<<<< ", ">>>>>>> ")


def _resolve_changelog_conflicts(changelog_path: Path) -> bool:
    """Drop conflict marker lines, keeping every entry from both sides (#3587).

    Returns False when the file is missing or has no conflict markers.
    """
    if not changelog_path.is_file():
        return False
    lines = changelog_path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept: list[str] = []
    in_base = False  # diff3/zdiff3 base section: drop, both sides already carry it
    for line in lines:
        if line.startswith("||||||| "):
            in_base = True
        elif line.rstrip("\r\n") == "=======":
            in_base = False
        elif not in_base and not line.startswith(_CONFLICT_MARKER_PREFIXES):
            kept.append(line)
    if len(kept) == len(lines):
        return False
    changelog_path.write_text("".join(kept), encoding="utf-8")
    return True


def _ensure_rebased(
    worktree: Path,
    base_branch: str,
    allow_paths: list[str] | None = None,
) -> PublishResult | None:
    """fetch + rebase onto origin/<base> when HEAD is behind (#3221 / #3227).

    Returns None on success, PublishResult on failure (``DIRTY_WORKTREE`` or
    ``REBASE_CONFLICT``). Before rebasing, discards RUNTIME_DIR_EXCLUDES dirt
    that is outside allow_paths so nexus worktrees can rebase past jobs/chat
    noise.
    """
    paths = allow_paths or []
    _run_git(worktree, "fetch", "origin", base_branch)
    ancestor = _run_git(
        worktree,
        "merge-base",
        "--is-ancestor",
        f"origin/{base_branch}",
        "HEAD",
        check=False,
    )
    if ancestor.returncode == 0:
        return None

    _discard_runtime_dir_dirt(worktree, paths)

    dirty_in_allow = [p for p in _dirty_paths(worktree) if _matches_allow_paths(p, paths)]
    if dirty_in_allow:
        return PublishResult(
            status="DIRTY_WORKTREE",
            stderr="\n".join(dirty_in_allow),
            exit_code=1,
        )

    rebase = _run_git(worktree, "rebase", f"origin/{base_branch}", check=False)
    while True:
        if rebase.returncode == 0:
            return None
        unmerged = _run_git(
            worktree, "diff", "--name-only", "--diff-filter=U", check=False
        ).stdout
        conflict_files = [p for p in unmerged.splitlines() if p.strip()]
        # CHANGELOG.md is appended by every parallel Issue; keep both sides (#3587).
        # Any other conflicting path aborts the whole rebase (no partial resolve).
        if conflict_files != [_CHANGELOG] or not _resolve_changelog_conflicts(
            worktree / _CHANGELOG
        ):
            break
        _run_git(worktree, "add", "--", _CHANGELOG)
        rebase = subprocess.run(
            ["git", "-C", str(worktree), "rebase", "--continue"],
            env={**os.environ, "GIT_EDITOR": "true"},
            capture_output=True,
            text=True,
        )

    _run_git(worktree, "rebase", "--abort", check=False)
    return PublishResult(
        status="REBASE_CONFLICT",
        stderr="\n".join(conflict_files) if conflict_files else (rebase.stderr or "").strip(),
        exit_code=1,
    )


# Transient push failures (network, auth refresh) are retried before giving up
# (sumipan/nexus#3680). Delays in seconds between attempts; patched to () in tests.
_PUSH_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)
_PUSH_REJECTED_MARKERS = ("[rejected]", "stale info", "non-fast-forward", "fetch first")


def _push_with_retry(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git push`` with ``check=False``; retry on failures that are not rejections."""
    attempts = len(_PUSH_RETRY_DELAYS) + 1
    result = _run_git(worktree, "push", *args, check=False)
    for attempt in range(1, attempts):
        if result.returncode == 0:
            break
        err = (result.stderr or result.stdout or "")
        if any(marker in err for marker in _PUSH_REJECTED_MARKERS):
            break
        print(
            f"git push failed (attempt {attempt}/{attempts}): {err.strip()[-300:]}",
            file=sys.stderr,
        )
        time.sleep(_PUSH_RETRY_DELAYS[attempt - 1])
        result = _run_git(worktree, "push", *args, check=False)
    return result


def _push_failed(pushed: subprocess.CompletedProcess[str], what: str) -> PublishResult:
    err = (pushed.stderr or pushed.stdout or "").strip() or (
        f"{what} failed with exit {pushed.returncode}"
    )
    return PublishResult(status="PUSH_FAILED", stderr=err, exit_code=1)


def _push_branch(worktree: Path, branch: str) -> PublishResult | None:
    """Push ``branch`` with rebase-aware ``--force-with-lease`` (#3237).

    Plain pushes never raise: a failure after retries is returned as
    ``PUSH_FAILED`` with git's stderr so the step reports it instead of
    crashing with a bare ``CalledProcessError`` (sumipan/nexus#3680).

    After ``_ensure_rebased``, a previously pushed tip may no longer be an
    ancestor of HEAD (same content, new SHAs). Plain ``git push`` then fails
    non-fast-forward. When the remote tip is not an ancestor, push with
    ``--force-with-lease=<branch>:<remote_sha>`` so a concurrent unknown
    remote update is rejected (``PUSH_DIVERGED``) instead of overwritten.
    """
    # Explicit refspec: clones made with a single-branch fetch config
    # (``+refs/heads/main:refs/remotes/origin/main``, the default for the /var/tmp/<repo>
    # clones) never create ``origin/<branch>`` from a bare ``git fetch origin <branch>``, so
    # every second publish believed the branch was new and pushed without the lease
    # (rejected non-fast-forward; sumipan/nexus#3628 gen 3, 2026-09-25).
    _run_git(
        worktree,
        "fetch",
        "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        check=False,
    )
    remote_ref = _run_git(worktree, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}", check=False)
    if remote_ref.returncode != 0:
        pushed = _push_with_retry(worktree, "-u", "origin", branch)
        if pushed.returncode != 0:
            return _push_failed(pushed, "git push -u origin")
        return None

    remote_sha = remote_ref.stdout.strip()
    ahead = _run_git(
        worktree, "rev-list", "--count", f"HEAD..origin/{branch}"
    ).stdout.strip()
    behind = _run_git(
        worktree, "rev-list", "--count", f"origin/{branch}..HEAD"
    ).stdout.strip()
    print(f"rev-list ahead={ahead} behind={behind}")

    ancestor = _run_git(
        worktree,
        "merge-base",
        "--is-ancestor",
        f"origin/{branch}",
        "HEAD",
        check=False,
    )
    if ancestor.returncode == 0:
        pushed = _push_with_retry(worktree, "-u", "origin", branch)
        if pushed.returncode != 0:
            return _push_failed(pushed, "git push -u origin")
        return None

    pushed = _push_with_retry(
        worktree,
        f"--force-with-lease={branch}:{remote_sha}",
        "-u",
        "origin",
        branch,
    )
    if pushed.returncode == 0:
        return None
    err = (pushed.stderr or pushed.stdout or "").strip() or (
        f"git push --force-with-lease failed with exit {pushed.returncode}"
    )
    if any(marker in err for marker in _PUSH_REJECTED_MARKERS):
        return PublishResult(status="PUSH_DIVERGED", stderr=err, exit_code=1)
    return PublishResult(status="PUSH_FAILED", stderr=err, exit_code=1)


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

    rebase_result = _ensure_rebased(worktree, base_branch, allow_paths)
    if rebase_result is not None:
        return rebase_result

    gate_fail = _check_commit_diff_gates(worktree, base_branch)
    if gate_fail is not None:
        return gate_fail

    bump_fail = _maybe_bump_version(worktree, base_branch, repo, issue_repo)
    if bump_fail is not None:
        return bump_fail

    if _ahead_commit_count(worktree, base_branch) == 0:
        return PublishResult(status="NO_DIFF", exit_code=1)

    push_result = _push_branch(worktree, branch)
    if push_result is not None:
        return push_result

    client = get_forge(repo=repo)
    try:
        existing = client.pr_list(head=branch, state="all", limit=1)
    except Exception as exc:  # noqa: BLE001 - rate limit / network: report, do not crash (#3767)
        return PublishResult(status="PR_LIST_FAILED", stderr=str(exc), exit_code=1)
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
