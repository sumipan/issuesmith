#!/usr/bin/env python3
"""Deterministic publish helper for issuesmith P3 (#2742)."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from ghdag.forge import get_forge

from issuesmith.config import get_config
from issuesmith.gate_rules.cp1 import (
    check_test_version_exact_assert,
    check_version_line_in_diff,
)
from issuesmith.scope_gate import parse_allow_paths_from_ctx

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

# Placeholder context_hook writes when the Issue has no allow_paths. It keeps the
# full-width parentheses scope_gate matches on; any full-width-parenthesised
# placeholder (including the legacy one in frozen orders) still means "no filter".
_UNRESTRICTED_ALLOW_PATHS = "\N{FULLWIDTH LEFT PARENTHESIS}unrestricted\N{FULLWIDTH RIGHT PARENTHESIS}"


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

    Empty / the unrestricted placeholder / None → None (no allow_paths filtering).
    """
    if raw is None:
        return None
    text = raw.strip()
    # scope_gate owns placeholder detection; it parses a placeholder to no paths.
    if not parse_allow_paths_from_ctx(text):
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

    # Backward compat: without allow_paths, keep the old git add -A minus RUNTIME_LOG_EXCLUDES
    if allow_paths is None:
        if not any(not _is_runtime_log(p) for p in dirty):
            return
        _run_git(worktree, "add", "-A")
        staged = _run_git(worktree, "diff", "--cached", "--name-only").stdout.splitlines()
        exclude_staged = [path for path in staged if _is_runtime_log(path)]
        if exclude_staged:
            _report_excluded(exclude_staged)
            _run_git(worktree, "reset", "HEAD", "--", *exclude_staged)
        _run_git(worktree, "commit", "-m", f"Implement Issue #{issue_number}")
        return

    candidates, excluded = _select_commit_candidates(dirty, allow_paths)
    _report_excluded(excluded)
    if not candidates:
        return
    _run_git(worktree, "add", "--", *candidates)
    _run_git(worktree, "commit", "-m", f"Implement Issue #{issue_number}")


def _ahead_commit_count(worktree: Path, base_branch: str) -> int:
    out = _run_git(worktree, "rev-list", "--count", f"origin/{base_branch}..HEAD").stdout.strip()
    return int(out or "0")


def _build_pr_metadata(
    issue_number: int,
    repo: str,
    issue_repo: str,
    target_count: int = 1,
) -> tuple[str, str]:
    # Never use GitHub's "Closes #N" auto-close (regardless of target_count or
    # same-repo / cross-repo). Closing the issue is always M2 finalize's job (the
    # merge-done transition on the issue_repo side + issue_close()); GitHub's
    # auto-close can fire earlier and was a mechanism with strong side effects.
    # Observed: #2852 (diary companion side) and #2873 (a regression in da499bf that
    # made cross-repo qualified Closes actually fire) were two premature-close
    # incidents of the same class. "Refs #N" stays as the PR body search marker used
    # by queue.py (see _closes_issue_marker).
    pack = get_config().language
    body = pack.message("publish.pr_body")
    if repo != issue_repo:
        return (
            pack.message("publish.pr_title_cross_repo", issue_repo=issue_repo, issue=issue_number),
            f"{body}\n\nRefs {issue_repo}#{issue_number}",
        )
    return (
        pack.message("publish.pr_title", issue=issue_number),
        f"{body}\n\nRefs #{issue_number}",
    )


def _run_version_bump(worktree: Path, base_branch: str) -> subprocess.CompletedProcess[str]:
    # Prefer the scripts/ shim so frozen orders and subprocess callers keep working.
    script = get_config().root / "scripts" / "issuesmith-version-bump.py"
    return subprocess.run(
        [sys.executable, str(script), "--worktree", str(worktree), "--base", base_branch],
        capture_output=True,
        text=True,
    )


# Public name for callers outside publish (M1 version_behind_base gate, nexus #3936).
run_version_bump = _run_version_bump


_BUMP_SUBJECT_PREFIX = "chore: bump version to "
_PLUS_VERSION_VALUE_RE = re.compile(r"""^\+\s*version\s*=\s*["']([^"']+)["']""")
_MINUS_VERSION_LINE_RE = re.compile(r"""^-\s*version\s*=\s*["']""")


def _bump_versions_in_range(worktree: Path, base_branch: str) -> list[str]:
    """Publish-made bump versions anywhere in ``origin/<base>..HEAD`` (newest first).

    CP2 FAIL → P1 re-run can stack implementation commits above an earlier bump commit.
    Range-wide detection keeps diff gates and idempotent bumping correct (#3922 / #4351).
    """
    log = _run_git(
        worktree, "log", "--format=%s", f"origin/{base_branch}..HEAD", check=False
    ).stdout.splitlines()
    versions: list[str] = []
    for subject in log:
        if not subject or not subject.startswith(_BUMP_SUBJECT_PREFIX):
            continue
        rest = subject[len(_BUMP_SUBJECT_PREFIX) :]
        token = rest.split(None, 1)[0] if rest.split(None, 1) else ""
        if token:
            versions.append(token)
    return versions


def _section_is_pyproject(section: list[str]) -> bool:
    for line in section:
        if line.startswith("diff --git "):
            if re.search(r"[ab]/(?:.+/)?pyproject\.toml\b", line):
                return True
        elif line.startswith("+++ b/"):
            if line[6:].rstrip().endswith("pyproject.toml"):
                return True
    return False


def _strip_bump_version_lines(diff: str, versions: list[str]) -> str:
    """Drop publish bump ``version =`` hunks from a unified diff before CP1 gates."""
    if not versions:
        return diff
    allowed = set(versions)
    sections: list[list[str]] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git ") and current:
            sections.append(current)
            current = []
        current.append(line)
    if current:
        sections.append(current)

    out: list[str] = []
    for section in sections:
        if not _section_is_pyproject(section):
            out.extend(section)
            continue
        plus_remove: set[int] = set()
        minus_indices: list[int] = []
        for idx, line in enumerate(section):
            plus_match = _PLUS_VERSION_VALUE_RE.match(line.rstrip("\r\n"))
            if plus_match and plus_match.group(1) in allowed:
                plus_remove.add(idx)
            elif _MINUS_VERSION_LINE_RE.match(line.rstrip("\r\n")):
                minus_indices.append(idx)
        remove = plus_remove | set(minus_indices[: len(plus_remove)])
        out.extend(line for idx, line in enumerate(section) if idx not in remove)
    return "".join(out)


def _maybe_bump_version(
    worktree: Path,
    base_branch: str,
    repo: str,
    issue_repo: str,
) -> PublishResult | None:
    """Run the deterministic bump only for cross-repo publishes that have pyproject.toml.

    Returns PublishResult(status="BUMP_FAILED") on failure, None on success or skip.
    Does not bump again when a publish bump commit is already in range (rerun idempotency, #3794).
    """
    if repo == issue_repo:
        return None
    if not (worktree / "pyproject.toml").is_file():
        return None
    if _bump_versions_in_range(worktree, base_branch):
        print(
            f"version bump skipped: a publish bump commit is already in "
            f"origin/{base_branch}..HEAD"
        )
        return None

    # origin/<base>, not the local branch: a stale local main drags old bump commits
    # and other PRs into the range and self-feeds B1 (#4508).
    result = _run_version_bump(worktree, f"origin/{base_branch}")
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        err = (result.stderr or "").strip() or f"version bump exited {result.returncode}"
        return PublishResult(status="BUMP_FAILED", stderr=err, exit_code=1)
    return None


def _check_commit_diff_gates(worktree: Path, base_branch: str) -> PublishResult | None:
    """Check version lines / exact-match test asserts after commit, before bump (#3065).

    Uses a three-dot diff (from the merge-base). A two-dot diff would show a reverse
    version diff just because base moved ahead, a false positive (#3221).
    """
    bump_versions = _bump_versions_in_range(worktree, base_branch)
    diff = _run_git(worktree, "diff", f"origin/{base_branch}...HEAD").stdout
    diff = _strip_bump_version_lines(diff, bump_versions)
    violations = check_version_line_in_diff(diff) + check_test_version_exact_assert(diff)
    if not violations:
        return None
    msgs = "\n".join(f"[{v.rule_id}] {v.fix_hint or v.message}" for v in violations)
    return PublishResult(status="P3_GATE_FAILED", stderr=msgs, exit_code=1)


_POST_BUMP_TESTS_RULE = "publish.post_bump_tests"
_POST_BUMP_TESTS_TIMEOUT = 600
_POST_BUMP_TESTS_TAIL_LINES = 40
# :(glob) so ``**`` also matches zero directories (tests/test_x.py).
_POST_BUMP_TESTS_PATHSPEC = ":(glob)tests/**/*.py"


def _post_bump_tests_failed(message: str) -> PublishResult:
    return PublishResult(
        status="P3_GATE_FAILED",
        stderr=f"[{_POST_BUMP_TESTS_RULE}] {message}",
        exit_code=1,
    )


def _output_tail(stdout: str | bytes | None, stderr: str | bytes | None) -> str:
    lines: list[str] = []
    for stream in (stdout, stderr):
        if isinstance(stream, bytes):
            stream = stream.decode("utf-8", errors="replace")
        lines.extend((stream or "").splitlines())
    return "\n".join(lines[-_POST_BUMP_TESTS_TAIL_LINES:])


def _has_folded_bump_commit(worktree: Path, base_branch: str) -> bool | PublishResult:
    """True when a publish bump commit in range changed CHANGELOG.md (read from Git, #5128)."""
    log = _run_git(
        worktree, "log", "--format=%H %s", f"origin/{base_branch}..HEAD", check=False
    )
    if log.returncode != 0:
        return _post_bump_tests_failed(
            f"git log origin/{base_branch}..HEAD exited {log.returncode}: "
            f"{(log.stderr or '').strip()}"
        )
    for line in log.stdout.splitlines():
        sha, _, subject = line.partition(" ")
        if not subject.startswith(_BUMP_SUBJECT_PREFIX):
            continue
        files = _run_git(
            worktree, "diff-tree", "--no-commit-id", "--name-only", "-r", sha, check=False
        )
        if files.returncode != 0:
            return _post_bump_tests_failed(
                f"git diff-tree {sha} exited {files.returncode}: "
                f"{(files.stderr or '').strip()}"
            )
        if "CHANGELOG.md" in files.stdout.splitlines():
            return True
    return False


def _post_bump_tests(worktree: Path, base_branch: str) -> PublishResult | None:
    """Re-run CHANGELOG-reading tests when a bump commit folded CHANGELOG.md (#5128).

    The fold empties ``## Unreleased``; downstream tests that read it can turn red only
    after the bump. Runs on every publish while a folded bump commit is in range, so a
    re-run with fix commits stacked on top re-verifies the current tree before push.
    """
    folded = _has_folded_bump_commit(worktree, base_branch)
    if isinstance(folded, PublishResult):
        return folded
    if not folded:
        return None

    grep = _run_git(
        worktree, "grep", "-l", "-E", "CHANGELOG", "--", _POST_BUMP_TESTS_PATHSPEC,
        check=False,
    )
    if grep.returncode == 1:
        return None
    if grep.returncode != 0:
        return _post_bump_tests_failed(
            f"git grep for CHANGELOG-reading tests exited {grep.returncode}: "
            f"{(grep.stderr or '').strip()}"
        )
    files = [f for f in grep.stdout.splitlines() if f]
    if not files:
        return None

    targets = " ".join(files)
    cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
    try:
        proc = subprocess.run(
            cmd,
            cwd=worktree,
            capture_output=True,
            text=True,
            timeout=_POST_BUMP_TESTS_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        return _post_bump_tests_failed(
            f"pytest timeout after {_POST_BUMP_TESTS_TIMEOUT}s on CHANGELOG-reading "
            f"tests after the CHANGELOG fold\ntargets: {targets}\n"
            f"{_output_tail(exc.stdout, exc.stderr)}"
        )
    if proc.returncode != 0:
        return _post_bump_tests_failed(
            f"pytest exited {proc.returncode} on CHANGELOG-reading tests after the "
            f"CHANGELOG fold\ntargets: {targets}\n"
            f"{_output_tail(proc.stdout, proc.stderr)}"
        )
    print(f"post-bump tests passed: {targets}")
    return None


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


# Transient fetch failures (shared .git ref lock, network) are retried before
# giving up (sumipan/nexus#4747). Delays in seconds between attempts.
_FETCH_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)


def _fetch_failure_detail(
    returncode: int, stderr: str | None, stdout: str | None, base_branch: str
) -> str:
    return (stderr or "").strip() or (stdout or "").strip() or (
        f"git fetch origin {base_branch} failed with exit {returncode}"
    )


def _fetch_with_retry(worktree: Path, base_branch: str) -> PublishResult | None:
    """``git fetch origin <base>`` with backoff; ``FETCH_FAILED`` once retries run out."""
    attempts = len(_FETCH_RETRY_DELAYS) + 1
    detail = ""
    for attempt in range(1, attempts + 1):
        try:
            fetched = _run_git(worktree, "fetch", "origin", base_branch)
            returncode, stderr, stdout = fetched.returncode, fetched.stderr, fetched.stdout
        except subprocess.CalledProcessError as exc:
            returncode, stderr, stdout = exc.returncode, exc.stderr, exc.output
        if returncode == 0:
            return None
        detail = _fetch_failure_detail(returncode, stderr, stdout, base_branch)
        if attempt == attempts:
            break
        print(
            f"git fetch failed (attempt {attempt}/{attempts}): {detail[-300:]}",
            file=sys.stderr,
        )
        time.sleep(_FETCH_RETRY_DELAYS[attempt - 1])
    return PublishResult(status="FETCH_FAILED", stderr=detail, exit_code=1)


def _ensure_rebased(
    worktree: Path,
    base_branch: str,
    allow_paths: list[str] | None = None,
) -> PublishResult | None:
    """fetch + rebase onto origin/<base> when HEAD is behind (#3221 / #3227).

    Returns None on success, PublishResult on failure (``FETCH_FAILED``,
    ``DIRTY_WORKTREE`` or ``REBASE_CONFLICT``). Before rebasing, discards RUNTIME_DIR_EXCLUDES dirt
    that is outside allow_paths so nexus worktrees can rebase past jobs/chat
    noise.
    """
    paths = allow_paths or []
    fetch_fail = _fetch_with_retry(worktree, base_branch)
    if fetch_fail is not None:
        return fetch_fail
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
    conflict_msg = "\n".join(conflict_files) if conflict_files else (rebase.stderr or "").strip()
    if conflict_files:
        try:
            log_result = _run_git(
                worktree,
                "log", "--oneline",
                f"HEAD..origin/{base_branch}",
                "--", *conflict_files,
                check=False,
            )
            if log_result.returncode == 0 and log_result.stdout.strip():
                conflict_msg += (
                    f"\n\nConflicting commits on origin/{base_branch}:\n{log_result.stdout.strip()}"
                )
        except Exception:
            pass
    return PublishResult(
        status="REBASE_CONFLICT",
        stderr=conflict_msg,
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

    post_bump_fail = _post_bump_tests(worktree, base_branch)
    if post_bump_fail is not None:
        return post_bump_fail

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
        help='context_hook format: "- path1\\n- path2". Omit or pass the unrestricted placeholder for no filter.',
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=1,
        help="Number of Issue targets. The PR body uses Refs when it is 2 or more",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
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
    except subprocess.CalledProcessError as exc:
        # Keep git's own diagnosis instead of a bare traceback (sumipan/nexus#4747).
        cmd = " ".join(exc.cmd) if isinstance(exc.cmd, (list, tuple)) else str(exc.cmd)
        print(f"command failed with exit {exc.returncode}: {cmd}", file=sys.stderr)
        for stream in (exc.stderr, exc.output):
            if stream and stream.strip():
                print(stream.strip(), file=sys.stderr)
        return 1

    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.pr_url:
        print(f"PR_URL: {result.pr_url}")
    print(f"PUBLISH_STATUS: {result.status}")
    return result.exit_code


# The bump helpers are shared with the M1 version_behind_base gate (nexus #3936).
__all__ = [
    "PublishResult",
    "_bump_versions_in_range",
    "_run_version_bump",
    "main",
    "publish",
    "run_version_bump",
]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
