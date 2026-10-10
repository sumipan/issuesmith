"""LLM review gate for requires evaluation (#4986)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Mapping

from ghdag.workflow.gates import Violation

from issuesmith.config import ReviewConfig, get_config
from issuesmith.gates import GateBuildContext, GateBuildError


class ReviewGate:
    """Runs a configured review template and maps failure output to violations."""

    def __init__(
        self,
        review_cfg: ReviewConfig,
        step_id: str,
        worktree_path: Path,
        variables: Mapping[str, str],
    ) -> None:
        self._review_cfg = review_cfg
        self._step_id = step_id
        self._worktree_path = worktree_path
        self._variables = variables

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
        rc, stdout = run_guarded_output(
            self._review_cfg.role,
            str(template_dir / self._review_cfg.template),
            variables,
            [self._review_cfg.success_status],
            self._review_cfg.failure_status,
            cwd=str(self._worktree_path),
            tier=self._review_cfg.tier,
        )
        if rc == 0:
            return []
        return _violations_from_stdout(stdout, self._review_cfg, self._step_id)


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
    return ReviewGate(step.review, ctx.step_id, ctx.worktree_path, ctx.variables)
