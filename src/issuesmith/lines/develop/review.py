"""Review step placeholder; LLM review runs via dispatch requires."""

from __future__ import annotations

import os
import subprocess
import sys

from issuesmith.config import StepConfig
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.verbs import map_step_result


def _parse_context(args: list[str]) -> dict[str, str]:
    context: dict[str, str] = {}
    for arg in args:
        if "=" not in arg:
            raise ValueError(f"not in key=value form: {arg!r}")
        key, value = arg.split("=", 1)
        context[key] = value
    return context


_AUTO_FIX_COMMIT_MESSAGE = "Apply gate auto-fixes"


def _commit_tracked_changes(worktree: str) -> str:
    """Commit tracked gate auto-fixes; return git stderr on failure, else \"\"."""
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if not (status.stdout or "").strip():
        return ""
    add = subprocess.run(
        ["git", "add", "-u"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        return add.stderr.strip() or "git add failed"
    commit = subprocess.run(
        ["git", "commit", "-m", _AUTO_FIX_COMMIT_MESSAGE],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        return commit.stderr.strip() or "git commit failed"
    return ""


def _git_diff(worktree: str, base_branch: str) -> str:
    proc = subprocess.run(
        ["git", "diff", f"origin/{base_branch}...HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return proc.stderr.strip() or "git diff failed"
    return proc.stdout


def _enrich_review_context(context: dict[str, str]) -> dict[str, str]:
    """Add ReviewGate optional variables for ``requires: [review]``."""
    merged = dict(context)
    issue = int(merged["issue_number"])
    worktree = merged["worktree_path"]
    base_branch = merged["base_branch"]
    body = str(get_forge().issue_get(issue, fields=["body"]).get("body") or "")
    merged["issue_body"] = body
    merged["diff"] = _git_diff(worktree, base_branch)
    return merged


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    _ = ctx, step
    return StepResult(status="done", markers=["REVIEW_READY"])


def _cli_dispatch() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: python3 -m issuesmith.lines.develop.review dispatch key=value ...",
            file=sys.stderr,
        )
        return 2
    raw = _parse_context(sys.argv[2:])
    worktree = raw.get("worktree_path", "")
    if not worktree:
        print("worktree_path is required for review dispatch", file=sys.stderr)
        return 2
    err = _commit_tracked_changes(worktree)
    if err:
        return map_step_result(
            StepResult(status="andon", andon=Andon(kind="broken", summary=err)),
            step_id="review",
            context=raw,
        )
    context = _enrich_review_context(raw)
    cmd = [sys.executable, "-m", "issuesmith", "dispatch", "review"]
    for key, value in context.items():
        cmd.append(f"{key}={value}")
    os.execv(cmd[0], cmd)
    return 0  # unreachable


if __name__ == "__main__":
    if sys.argv[1:2] == ["dispatch"]:
        raise SystemExit(_cli_dispatch())
    raise SystemExit(
        "usage: python3 -m issuesmith.lines.develop.review dispatch key=value ...",
    )
