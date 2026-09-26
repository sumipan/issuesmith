"""M1 gates: version_behind_base (nexus #3936).

Two Issues on the same repo publish in parallel and both bump ``origin/<base>``'s
version to the same next version (P3 only bumps cross-repo branches). The first
merge gets the release tag; the second lands with the same version, the release
workflow skips the existing tag, and its changes never reach a release.

M1 runs this gate right before merging: a branch whose pyproject version is not
ahead of ``origin/<base>`` is caught up with the base and bumped again.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ghdag.workflow.gates import Violation

from issuesmith.gates.base import ContractInput
from issuesmith.ops.publish import run_version_bump

RULE_ID = "m1.version_behind_base"

_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _version_key(version: str) -> tuple[int, int, int] | None:
    parts = version.split(".")
    if len(parts) < 3:
        return None
    nums: list[int] = []
    for part in parts[:3]:
        m = re.match(r"^(\d+)", part)
        if not m:
            return None
        nums.append(int(m.group(1)))
    return nums[0], nums[1], nums[2]


class VersionBehindBaseGate:
    """Fail when the branch's pyproject version is not ahead of ``origin/<base>``.

    fix() merges ``origin/<base>`` into the branch (M1 only calls it for a CLEAN PR,
    so the merge has no conflicts), re-runs the deterministic version bump from the
    base's version and pushes. Any failure resets HEAD to where it was and raises.
    """

    def __init__(self, worktree_path: Path, base_branch: str) -> None:
        self._root = worktree_path
        self._base = base_branch

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self._root), *args],
            capture_output=True, text=True, check=False,
        )

    def _version_at(self, ref: str) -> str | None:
        shown = self._git("show", f"{ref}:pyproject.toml")
        if shown.returncode != 0:
            return None
        m = _VERSION_RE.search(shown.stdout)
        return m.group(1) if m else None

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if not (self._root / "pyproject.toml").is_file():
            return []
        branch_ver = self._version_at("HEAD")
        if branch_ver is None:
            return []
        fetch = self._git("fetch", "origin", self._base)
        if fetch.returncode != 0:
            return [Violation(
                rule_id=RULE_ID,
                severity="fail",
                message=f"git fetch origin {self._base} failed: {fetch.stderr.strip()}",
                location="pyproject.toml",
                auto_fixable=False,
                fix_hint=f"Manually fetch origin/{self._base} and re-run M1",
            )]
        base_ver = self._version_at(f"origin/{self._base}")
        if base_ver is None:
            return []
        branch_key, base_key = _version_key(branch_ver), _version_key(base_ver)
        if branch_key is None or base_key is None or branch_key > base_key:
            return []
        return [Violation(
            rule_id=RULE_ID,
            severity="fail",
            message=(
                f"branch version {branch_ver} is not ahead of origin/{self._base}"
                f" version {base_ver}; merging would reuse an existing release tag"
            ),
            location="pyproject.toml",
            auto_fixable=True,
            fix_hint=f"merge origin/{self._base} and bump the version past {base_ver}",
        )]

    def fix(self, inp: ContractInput) -> ContractInput:
        violations = self.check(inp.body, inp.labels)
        if not violations:
            return inp
        if not all(v.auto_fixable for v in violations):
            raise RuntimeError(f"{RULE_ID}: {violations[0].message}")
        dirty = self._git("status", "--porcelain", "--untracked-files=no").stdout.strip()
        if dirty:
            raise RuntimeError(f"{RULE_ID} auto-fix refused: worktree has uncommitted changes")

        orig = self._git("rev-parse", "HEAD").stdout.strip()
        try:
            merge = self._git("merge", "--no-edit", f"origin/{self._base}")
            if merge.returncode != 0:
                self._git("merge", "--abort")
                detail = (merge.stderr or merge.stdout or "").strip()[-400:]
                raise RuntimeError(f"{RULE_ID}: merge of origin/{self._base} failed: {detail}")
            bump = run_version_bump(self._root, f"origin/{self._base}")
            if bump.returncode != 0:
                detail = (bump.stderr or "").strip() or f"version bump exited {bump.returncode}"
                raise RuntimeError(f"{RULE_ID}: {detail}")
            remaining = self.check(inp.body, inp.labels)
            if remaining:
                raise RuntimeError(f"{RULE_ID}: still behind after bump: {remaining[0].message}")
            push = self._git("push", "origin", "HEAD")
            if push.returncode != 0:
                raise RuntimeError(f"{RULE_ID}: git push failed: {push.stderr.strip()[-400:]}")
        except RuntimeError:
            self._git("reset", "--hard", orig)
            raise
        return inp


__all__ = ["RULE_ID", "VersionBehindBaseGate"]
