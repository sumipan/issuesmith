"""LLM review gate for requires evaluation (#4986).

When ``steps.<id>.tier`` is ``pr_diff``, diff lines and unchecked AC count select
the review engine tier; otherwise ``steps.<id>.review.tier`` is passed through.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Mapping

from issuesmith.config import ReviewConfig, TierConfig, get_config
from issuesmith.gate_registry import Violation
from issuesmith.gate_rules.ac_complete_rules import get_unchecked_count
from issuesmith.gates import GateBuildContext, GateBuildError
from issuesmith.pr_scope import DIFF_LINES_FALLBACK
from issuesmith.step_tier import pr_diff_tier

_DIFF_TIMEOUT_SEC = 30


class ReviewGate:
    """Runs a configured review template and maps failure output to violations."""

    def __init__(
        self,
        review_cfg: ReviewConfig,
        step_id: str,
        worktree_path: Path,
        variables: Mapping[str, str],
        *,
        tier_cfg: TierConfig | None = None,
        base_branch: str = "main",
    ) -> None:
        self._review_cfg = review_cfg
        self._step_id = step_id
        self._worktree_path = worktree_path
        self._variables = variables
        self._tier_cfg = tier_cfg
        self._base_branch = base_branch

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        from issuesmith.engine import run_guarded_output

        template_dir = get_config().paths.template_dir
        # Orders frozen before a template gained a variable lack its k=v (#5143).
        merged = dict(self._variables)
        filled: list[str] = []
        for key in self._review_cfg.optional_variables:
            if key not in merged:
                merged[key] = ""
                filled.append(key)
        if filled:
            print(
                f"[issuesmith-review] step={self._step_id} optional variables "
                f"missing from order, filled empty: {', '.join(filled)}",
                file=sys.stderr,
            )
        variables = [f"{k}={v}" for k, v in merged.items()]
        engine_tier = self._resolve_tier(body)
        rc, stdout = run_guarded_output(
            self._review_cfg.role,
            str(template_dir / self._review_cfg.template),
            variables,
            [self._review_cfg.success_status],
            self._review_cfg.failure_status,
            cwd=str(self._worktree_path),
            tier=engine_tier,
        )
        if rc == 0:
            return []
        if _stdout_is_review_result(stdout, self._review_cfg):
            return _violations_from_stdout(stdout, self._review_cfg, self._step_id)
        return [_engine_error_violation(rc, stdout, self._step_id)]

    def _resolve_tier(self, body: str) -> str | None:
        if self._tier_cfg is None or self._tier_cfg.kind != "pr_diff":
            return self._review_cfg.tier
        diff_lines, fallback = _worktree_diff_lines(
            self._worktree_path, self._base_branch
        )
        unchecked = get_unchecked_count(body)
        tier = pr_diff_tier(
            diff_lines,
            unchecked,
            True,
            light_max_diff_lines=self._tier_cfg.light_max_diff_lines,
        )
        msg = (
            f"[issuesmith-review] step={self._step_id} tier={tier} "
            f"diff_lines={diff_lines} unchecked_ac={unchecked}"
        )
        if fallback:
            msg = f"{msg} diff_fallback={fallback}"
        print(msg, file=sys.stderr)
        return tier


def _worktree_diff_lines(
    worktree: Path, base_branch: str
) -> tuple[int, str | None]:
    ref = f"origin/{base_branch}...HEAD"
    try:
        proc = subprocess.run(
            ["git", "diff", "--numstat", ref],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            timeout=_DIFF_TIMEOUT_SEC,
        )
    except (OSError, subprocess.TimeoutExpired):
        return DIFF_LINES_FALLBACK, "diff_command_failed"
    if proc.returncode != 0:
        return DIFF_LINES_FALLBACK, "diff_failed"
    total = 0
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            return DIFF_LINES_FALLBACK, "invalid_numstat"
        for count in parts[:2]:
            if count == "-":
                continue
            try:
                value = int(count)
            except ValueError:
                return DIFF_LINES_FALLBACK, "invalid_numstat"
            if value < 0:
                return DIFF_LINES_FALLBACK, "invalid_numstat"
            total += value
    return total, None


def _stdout_is_review_result(stdout: str, review_cfg: ReviewConfig) -> bool:
    from issuesmith.engine import extract_status_values

    heading = review_cfg.problems_heading.strip()
    for line in stdout.splitlines():
        if line.strip() == heading:
            return True
    if review_cfg.failure_status in extract_status_values(stdout):
        return True
    failure_marker = f"PIPELINE_STATUS: {review_cfg.failure_status}"
    for line in stdout.splitlines():
        if line.strip() == failure_marker:
            return True
    return False


def _engine_error_violation(rc: int, stdout: str, step_id: str) -> Violation:
    if stdout:
        tail = stdout[-200:] if len(stdout) > 200 else stdout
        detail = f"review engine exited {rc}: {tail}"
    else:
        detail = "review failed without output"
    rule_id = f"review.{step_id}.engine_error"
    return Violation(
        rule_id=rule_id,
        severity="fail",
        message=detail,
        location=None,
        auto_fixable=False,
        fix_hint=detail,
    )


def _violations_from_stdout(
    stdout: str,
    review_cfg: ReviewConfig,
    step_id: str,
) -> list[Violation]:
    heading = review_cfg.problems_heading.strip()
    lines = stdout.splitlines()
    heading_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            heading_idx = i

    messages: list[str] = []
    if heading_idx is not None:
        for line in lines[heading_idx + 1 :]:
            stripped = line.strip()
            if not stripped:
                break
            if stripped.startswith("PIPELINE_STATUS:"):
                break
            if stripped == "```ghdag" or (
                stripped.startswith("```") and stripped.endswith("ghdag")
            ):
                break
            if line.startswith("- "):
                messages.append(line[2:])
            else:
                break

    if not messages:
        snippet = stdout[:200] if stdout else "review failed without output"
        messages = [snippet]

    rule_id = f"review.{step_id}"
    return [
        Violation(
            rule_id=rule_id,
            severity="fail",
            message=msg,
            location=None,
            auto_fixable=False,
            fix_hint=msg,
        )
        for msg in messages
    ]


def build_review_gate(ctx: GateBuildContext) -> ReviewGate:
    if ctx.worktree_path is None:
        raise GateBuildError("gate 'review' requires worktree_path but context has none")
    if not ctx.step_id:
        raise GateBuildError("gate 'review' requires step_id in GateBuildContext")
    step = get_config().steps.get(ctx.step_id)
    if step is None or step.review is None:
        raise GateBuildError(
            f"steps.{ctx.step_id}.review is not configured"
        )
    return ReviewGate(
        step.review,
        ctx.step_id,
        ctx.worktree_path,
        ctx.variables,
        tier_cfg=step.tier,
        base_branch=ctx.base_branch,
    )
