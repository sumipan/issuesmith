"""Review step: branch freshness check, then review dispatch via requires."""

from __future__ import annotations

import functools
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

from issuesmith.cli_errors import fail
from issuesmith.config import StepConfig, get_config
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
_BRANCH_CHECK_TIMEOUT_SEC = 30
_OBJECT_ID_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_LANG_EN = Path(__file__).resolve().parents[2] / "lang" / "en.yaml"


def _is_object_id(value: str) -> bool:
    return bool(_OBJECT_ID_RE.fullmatch(value))


def _branch_name_valid(branch: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


@functools.lru_cache
def _package_en_messages() -> dict[str, str]:
    raw = yaml.safe_load(_LANG_EN.read_text(encoding="utf-8")) or {}
    messages = raw.get("messages", raw)
    if not isinstance(messages, dict):
        return {}
    return {str(k): str(v) for k, v in messages.items()}


def _language_message(key: str, /, **kwargs: object) -> str:
    pack = get_config().language
    if key in pack.messages:
        return pack.message(key, **kwargs)
    template = _package_en_messages().get(key)
    if template is None:
        raise KeyError(key)
    return template.format(**kwargs)


def _worktree_path(ctx: StepContext) -> Path | None:
    raw = ctx.worktree_path.strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return path
    return get_config().root / path


def _git_in_worktree(
    worktree: Path, args: list[str], *, timeout: int = _BRANCH_CHECK_TIMEOUT_SEC
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _tail_stderr(stderr: str) -> str:
    text = (stderr or "").strip()
    return text[-200:] if len(text) > 200 else text


def _branch_check_failed(detail: str) -> StepResult:
    summary = _language_message(
        "review.branch_check_failed",
        detail=detail or "branch check failed",
    )
    return StepResult(
        status="andon",
        andon=Andon(
            kind="broken",
            rule_id="review.branch_check_failed",
            summary=summary,
        ),
    )


def _branch_moved_andon(branch: str) -> StepResult:
    summary = _language_message("review.branch_moved", branch=branch)
    return StepResult(
        status="andon",
        andon=Andon(
            kind="blocked",
            rule_id="review.branch_moved",
            summary=summary,
        ),
    )


def _check_remote_branch_matches_head(worktree: Path, branch: str) -> StepResult | None:
    """Return a StepResult when the review must stop; None when check passes or is skipped."""
    if not _branch_name_valid(branch):
        return _branch_check_failed(f"invalid branch name: {branch}")
    try:
        head_proc = _git_in_worktree(worktree, ["rev-parse", "HEAD"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _branch_check_failed(str(exc))
    if head_proc.returncode != 0:
        return _branch_check_failed(_tail_stderr(head_proc.stderr))
    local_sha = head_proc.stdout.strip()
    if not local_sha:
        return _branch_check_failed("rev-parse returned empty HEAD")
    if not _is_object_id(local_sha):
        return _branch_check_failed("rev-parse returned invalid object id")

    ref = f"refs/heads/{branch}"
    try:
        remote_proc = _git_in_worktree(
            worktree, ["ls-remote", "--heads", "origin", ref]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _branch_check_failed(str(exc))
    if remote_proc.returncode != 0:
        return _branch_check_failed(_tail_stderr(remote_proc.stderr))

    stdout = (remote_proc.stdout or "").strip()
    if not stdout:
        return None
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if len(lines) != 1:
        return _branch_check_failed("unexpected ls-remote output")
    parts = lines[0].split("\t")
    if len(parts) != 2 or not parts[0].strip():
        return _branch_check_failed("unexpected ls-remote output")
    remote_sha = parts[0].strip()
    remote_ref = parts[1].strip()
    if remote_ref != ref:
        return _branch_check_failed("unexpected ls-remote output")
    if not _is_object_id(remote_sha):
        return _branch_check_failed("unexpected ls-remote output")
    if remote_sha == local_sha:
        return None
    return _branch_moved_andon(branch)


def _commit_tracked_changes(worktree: str) -> str:
    """Commit tracked gate auto-fixes; return git stderr on failure, else \"\"."""
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
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
        return add.stderr.strip() or f"git add failed (exit {add.returncode})"
    commit = subprocess.run(
        ["git", "commit", "-m", _AUTO_FIX_COMMIT_MESSAGE],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if commit.returncode != 0:
        return commit.stderr.strip() or f"git commit failed (exit {commit.returncode})"
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
    _ = step
    branch = ctx.branch.strip()
    if not branch:
        return StepResult(status="done", markers=["REVIEW_READY"])
    worktree = _worktree_path(ctx)
    if worktree is None:
        return _branch_check_failed("worktree_path is missing")
    blocked = _check_remote_branch_matches_head(worktree, branch)
    if blocked is not None:
        return blocked
    return StepResult(status="done", markers=["REVIEW_READY"])


_REVIEW_DISPATCH_HINT = (
    "usage: python3 -m issuesmith.lines.develop.review dispatch key=value ..."
)


def main(argv: list[str] | None = None) -> int:
    tokens = list(argv if argv is not None else sys.argv[1:])
    if not tokens:
        return fail(
            "MISSING_ARGUMENT",
            "subcommand required",
            _REVIEW_DISPATCH_HINT,
        )
    sub = tokens[0]
    if sub != "dispatch":
        return fail(
            "UNKNOWN_COMMAND",
            f"unknown subcommand: {sub!r}",
            _REVIEW_DISPATCH_HINT,
        )
    if len(tokens) < 2:
        return fail(
            "MISSING_ARGUMENT",
            "dispatch requires key=value arguments",
            _REVIEW_DISPATCH_HINT,
        )
    raw = _parse_context(tokens[1:])
    worktree = raw.get("worktree_path", "")
    if not worktree:
        return fail(
            "MISSING_ARGUMENT",
            "worktree_path is required for review dispatch",
            f"{_REVIEW_DISPATCH_HINT} (include worktree_path=PATH)",
        )
    err = _commit_tracked_changes(worktree)
    if err:
        return map_step_result(
            StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary=f"review: could not commit gate auto-fixes: {err}",
                ),
            ),
            step_id="review",
            context=raw,
        )
    context = _enrich_review_context(raw)
    cmd = [sys.executable, "-m", "issuesmith", "dispatch", "review"]
    for key, value in context.items():
        cmd.append(f"{key}={value}")
    os.execv(cmd[0], cmd)
    return 0  # unreachable


def _cli_dispatch() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
