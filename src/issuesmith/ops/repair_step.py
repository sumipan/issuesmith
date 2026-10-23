"""Explicit repair step module for requires violations (#4276).

Runs a single LLM repair cycle when dispatch requires evaluation finds
non-auto-fixable violations.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from pathlib import Path

import issuesmith.engine as engine
from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.engine import run_guarded_output

_REPAIR_ACTIVE_ENV = "ISSUESMITH_REPAIR_ACTIVE"
_SUMMARY_MAX_LEN = 600
_PATH_TOKEN_RE = re.compile(
    r"(?<![/\w])"
    r"([a-zA-Z0-9][a-zA-Z0-9_./-]*"
    r"\.(?:py|md|yaml|yml|toml|json|jsonl|txt))"
)


def _get_previous_commits(worktree_path: str, base_branch: str) -> str:
    """Return git log --oneline for commits ahead of origin/<base>."""
    if not worktree_path:
        return ""
    try:
        proc = subprocess.run(
            ["git", "log", "--oneline", f"origin/{base_branch}..HEAD"],
            capture_output=True,
            text=True,
            check=False,
            cwd=worktree_path,
        )
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except Exception:
        return ""


def _parse_path_patterns(raw: str) -> list[str]:
    return [p.lstrip("- ").strip() for p in raw.splitlines() if p.strip()]


def _path_allowed(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatch(path, pat) for pat in patterns)


def _lines_before_last_status(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    last_status_idx: int | None = None
    for i, line in enumerate(lines):
        if engine._STATUS_LINE_PREFIX_RE.match(line):
            last_status_idx = i
    pool = lines[:last_status_idx] if last_status_idx is not None else lines
    non_empty = [ln.strip() for ln in pool if ln.strip()]
    return non_empty[-8:]


def _build_gave_up_summary(ctx: StepContext, stdout: str) -> str:
    parts = [f"repair gave up in step {ctx.repair_step_origin or 'unknown'}"]
    tail = _lines_before_last_status(stdout)
    if tail:
        parts.append("\n".join(tail))
    if ctx.repair_violation_summary.strip():
        parts.append(ctx.repair_violation_summary.strip())
    text = "\n".join(parts)
    if len(text) > _SUMMARY_MAX_LEN:
        return text[:_SUMMARY_MAX_LEN]
    return text


def _stdout_path_candidates(stdout: str, worktree_path: str) -> set[str]:
    if not worktree_path:
        return set()
    root = Path(worktree_path)
    found: set[str] = set()
    for line in _lines_before_last_status(stdout):
        for match in _PATH_TOKEN_RE.finditer(line):
            path = match.group(1)
            if path.startswith("/") or ".." in path.split("/"):
                continue
            full = root / path
            if full.is_file():
                found.add(path)
    return found


def _widen_path_candidates(ctx: StepContext, stdout: str) -> list[str]:
    allow_patterns = _parse_path_patterns(ctx.allow_paths)
    derived_patterns = _parse_path_patterns(ctx.derived_allow_paths)
    structured = {
        p.strip()
        for p in ctx.repair_widen_candidates.splitlines()
        if p.strip()
    }
    candidates = structured | _stdout_path_candidates(stdout, ctx.worktree_path or ctx.target_worktree_path or "")
    out: list[str] = []
    for path in sorted(candidates):
        if path.startswith("/") or ".." in path.split("/"):
            continue
        if _path_allowed(path, allow_patterns) or _path_allowed(path, derived_patterns):
            continue
        out.append(path)
    return out


def _decision_options(ctx: StepContext, stdout: str) -> list[str]:
    widen_paths = _widen_path_candidates(ctx, stdout)
    options: list[str] = []
    if widen_paths:
        options.append(f"widen:{','.join(widen_paths)}")
    options.extend(["split", "reject"])
    return options


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute one LLM repair cycle.

    Returns andon(broken) when repair_violations is empty or repair is re-entered.
    Raises RetrySignal unchanged (not counted as a repair cycle).
    """
    if os.environ.get(_REPAIR_ACTIVE_ENV):
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                summary="repair step re-entered itself (ISSUESMITH_REPAIR_ACTIVE is set)",
            ),
        )

    if not ctx.repair_violations.strip():
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary="repair launched without violations"),
        )

    if step is None or not step.template:
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary="repair step has no template configured"),
        )

    template = str(get_config().paths.template_dir / step.template)
    worktree_path = ctx.worktree_path or ctx.target_worktree_path or ""

    previous_commits = _get_previous_commits(worktree_path, ctx.base_branch)
    variables = [
        f"repair_violations={ctx.repair_violations}",
        f"repair_step_origin={ctx.repair_step_origin}",
        f"issue_number={ctx.issue_number}",
        f"worktree_path={worktree_path}",
        f"branch={ctx.branch}",
        f"base_branch={ctx.base_branch}",
        f"previous_commits={previous_commits}",
    ]

    old_env = os.environ.get(_REPAIR_ACTIVE_ENV)
    os.environ[_REPAIR_ACTIVE_ENV] = "1"
    try:
        rc, stdout = run_guarded_output(
            "implementation",
            template,
            variables,
            success_statuses=["REPAIR_DONE"],
            failure_status="REPAIR_FAILED",
            cwd=worktree_path or None,
        )
    finally:
        if old_env is None:
            os.environ.pop(_REPAIR_ACTIVE_ENV, None)
        else:
            os.environ[_REPAIR_ACTIVE_ENV] = old_env

    statuses = engine._extract_status_values(stdout)
    final_status = statuses[-1] if statuses else None
    if final_status == "REPAIR_FAILED":
        return StepResult(
            status="andon",
            andon=Andon(
                kind="decision",
                rule_id="repair.gave_up",
                summary=_build_gave_up_summary(ctx, stdout),
                options=_decision_options(ctx, stdout),
            ),
        )

    if rc != 0:
        return StepResult(exit_code=rc)

    origin_id = ctx.repair_step_origin or ""
    origin_step = get_config().steps.get(origin_id)
    if (
        origin_step is not None
        and origin_step.repair.push
        and worktree_path
    ):
        try:
            push_proc = subprocess.run(
                ["git", "push", "origin", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                cwd=worktree_path,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary="repair push failed: timeout",
                ),
            )
        if push_proc.returncode != 0:
            err_tail = (push_proc.stderr or push_proc.stdout or "").strip()
            if len(err_tail) > 500:
                err_tail = err_tail[-500:]
            return StepResult(
                status="andon",
                andon=Andon(
                    kind="broken",
                    summary=f"repair push failed: {err_tail}",
                ),
            )

    return StepResult(status="done")
