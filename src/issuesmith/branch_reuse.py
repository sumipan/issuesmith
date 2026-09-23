"""branch_reuse.py — locate and record reusable previous-generation branches.

Provides three public functions:
  record_base      — write git config branch.<branch>.issuesmithbase
  find_reusable_branch — find latest local branch for an issue that can be reused
  previous_commits — list commits on a branch since its base
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

BASE_CONFIG_KEY = "issuesmithbase"

_BRANCH_RE = re.compile(r"^feat/issue-(\d+)-([a-f0-9]+)$")


def record_base(repo_dir: Path, branch: str, base: str) -> None:
    """Write git config branch.<branch>.issuesmithbase <base>. Silently ignores failures."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "config",
            f"branch.{branch}.{BASE_CONFIG_KEY}",
            base,
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode(errors="replace").strip()
        print(f"branch_reuse.record_base warning: {stderr}", file=sys.stderr)


def _resolve_base_ref(repo_dir: Path, base: str) -> str | None:
    """Return 'origin/<base>' or '<base>' if the ref exists, else None."""
    for ref in (f"refs/remotes/origin/{base}", f"refs/heads/{base}"):
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", ref],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            return f"origin/{base}" if ref.startswith("refs/remotes/") else base
    return None


def find_reusable_branch(repo_dir: Path, issue_number: int, base: str) -> str | None:
    """Return the most recent local branch for issue_number that can be reused.

    Returns None if no suitable branch is found. Never raises.
    """
    if not repo_dir.exists():
        return None

    git_check = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    if git_check.returncode != 0:
        return None

    base_ref = _resolve_base_ref(repo_dir, base)
    if base_ref is None:
        return None

    candidates_proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(refname:short)",
            f"refs/heads/feat/issue-{issue_number}-*",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if candidates_proc.returncode != 0:
        return None

    candidates = [
        line.strip()
        for line in candidates_proc.stdout.splitlines()
        if line.strip()
    ]

    pattern = re.compile(rf"^feat/issue-{re.escape(str(issue_number))}-[a-f0-9]+$")
    candidates = [c for c in candidates if pattern.match(c)]

    for cand in candidates:
        recorded_base_proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "config",
                f"branch.{cand}.{BASE_CONFIG_KEY}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if recorded_base_proc.returncode != 0:
            continue
        recorded_base = recorded_base_proc.stdout.strip()
        if recorded_base != base:
            continue

        ancestor_proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "merge-base",
                "--is-ancestor",
                cand,
                base_ref,
            ],
            capture_output=True,
            check=False,
        )
        if ancestor_proc.returncode == 0:
            continue

        count_proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "rev-list",
                "--count",
                f"{base_ref}..{cand}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if count_proc.returncode != 0:
            continue
        count_str = count_proc.stdout.strip()
        try:
            count = int(count_str)
        except ValueError:
            continue
        if count < 1:
            continue

        return cand

    return None


def previous_commits(repo_dir: Path, branch: str, base: str) -> list[str]:
    """Return commits on branch since base as '<sha7> <subject>' strings (newest first).

    Returns [] on any failure.
    """
    base_ref = _resolve_base_ref(repo_dir, base)
    if base_ref is None:
        return []

    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "log",
            "--format=%h %s",
            f"{base_ref}..{branch}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return lines
