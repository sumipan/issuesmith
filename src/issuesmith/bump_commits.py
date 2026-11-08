"""Shared helpers for publish-made version bump commits (#6177)."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

# Same value as issuesmith.ops.version_bump._BUMP_COMMIT_PREFIX (standalone script).
BUMP_SUBJECT_PREFIX = "chore: bump version to "

_PLUS_VERSION_VALUE_RE = re.compile(r"""^\+\s*version\s*=\s*["']([^"']+)["']""")
_MINUS_VERSION_LINE_RE = re.compile(r"""^-\s*version\s*=\s*["']""")


class BumpCommitsError(RuntimeError):
    """Git could not list or rewrite publish bump commits."""


def _run_git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise BumpCommitsError(detail or f"git {' '.join(args)} failed")
    return proc


def _rev_parse(worktree: Path, ref: str) -> str:
    return _run_git(worktree, "rev-parse", ref).stdout.strip()


def bump_versions_in_range(worktree: Path, base_branch: str) -> list[str]:
    """Publish bump versions in ``origin/<base>..HEAD`` (newest first)."""
    log = _run_git(
        worktree, "log", "--format=%s", f"origin/{base_branch}..HEAD"
    ).stdout.splitlines()
    versions: list[str] = []
    for subject in log:
        if not subject or not subject.startswith(BUMP_SUBJECT_PREFIX):
            continue
        rest = subject[len(BUMP_SUBJECT_PREFIX) :]
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


def strip_bump_version_lines(diff: str, versions: Sequence[str]) -> str:
    """Remove publish bump ``version =`` hunks from a unified diff."""
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


def _diff_change_line_count(diff: str) -> int:
    count = 0
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            count += 1
        elif line.startswith("-") and not line.startswith("---"):
            count += 1
    return count


def is_bump_only_pyproject_change(worktree: Path, base_branch: str) -> bool:
    """True when cumulative ``pyproject.toml`` diff is only publish bump version lines."""
    versions = bump_versions_in_range(worktree, base_branch)
    if not versions:
        return False
    diff = _run_git(
        worktree, "diff", f"origin/{base_branch}...HEAD", "--", "pyproject.toml"
    ).stdout
    stripped = strip_bump_version_lines(diff, versions)
    return _diff_change_line_count(stripped) == 0


def _bump_shas_oldest_first(worktree: Path, base_branch: str) -> list[str]:
    out = _run_git(
        worktree,
        "log",
        "--reverse",
        "--format=%H\t%s",
        f"origin/{base_branch}..HEAD",
    ).stdout
    shas: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, _, subject = line.partition("\t")
        if subject.startswith(BUMP_SUBJECT_PREFIX):
            shas.append(sha)
    return shas


def _rebase_abort(worktree: Path) -> None:
    subprocess.run(
        ["git", "-C", str(worktree), "rebase", "--abort"],
        capture_output=True,
        text=True,
        check=False,
    )


def _reset_hard(worktree: Path, ref: str) -> None:
    subprocess.run(
        ["git", "-C", str(worktree), "reset", "--hard", ref],
        capture_output=True,
        text=True,
        check=False,
    )


def drop_bump_commits_before_rebase(worktree: Path, base_branch: str) -> tuple[str, ...]:
    """Drop publish bump commits before rebasing onto the base branch.

    Returns the original SHAs oldest first. Commits are removed newest first so the
    remaining (older) SHAs stay in history, and chained bumps (0.2.0 -> 0.3.0) do not
    conflict on the ``version =`` line.
    """
    original_head = _rev_parse(worktree, "HEAD")
    shas = _bump_shas_oldest_first(worktree, base_branch)
    if not shas:
        return ()
    removed: list[str] = []
    for sha in reversed(shas):
        proc = subprocess.run(
            ["git", "-C", str(worktree), "rebase", "--onto", f"{sha}^", sha],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            _rebase_abort(worktree)
            _reset_hard(worktree, original_head)
            detail = (proc.stderr or proc.stdout or "").strip()
            raise BumpCommitsError(detail or f"failed to drop bump commit {sha[:7]}")
        removed.append(sha)
    return tuple(reversed(removed))


__all__ = [
    "BUMP_SUBJECT_PREFIX",
    "BumpCommitsError",
    "bump_versions_in_range",
    "drop_bump_commits_before_rebase",
    "is_bump_only_pyproject_change",
    "strip_bump_version_lines",
]
