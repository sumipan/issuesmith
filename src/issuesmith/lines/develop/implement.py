"""Run the implementation LLM for the reference develop line."""

from __future__ import annotations

import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from string import Template

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.gates.worktree import count_commits_excluding_wip
from issuesmith.verbs import pre_llm_violations, run_order


def _template_identifiers(template: Template) -> list[str]:
    ids: list[str] = []
    for mo in template.pattern.finditer(template.template):
        named = mo.group("named") or mo.group("braced")
        if named is not None and named not in ids:
            ids.append(named)
    return ids


def _commits_since_base(worktree: Path, base_branch: str) -> int:
    return count_commits_excluding_wip(worktree, f"origin/{base_branch}..HEAD")


def _context_from_step(ctx: StepContext) -> dict[str, str]:
    return {key: str(value) for key, value in asdict(ctx).items()}


def _render_order(
    ctx: StepContext,
    step: StepConfig,
    body: str,
) -> tuple[str | None, str | None, str | None]:
    """Render the implement template. Returns (order, issue_body_path, error_summary)."""
    if not step.template:
        return None, None, "implement step has no template configured"
    template_path = get_config().paths.template_dir / step.template
    template = Template(template_path.read_text(encoding="utf-8"))
    variables = {key: str(value) for key, value in asdict(ctx).items()}
    variables["issue_body"] = body
    needed = set(_template_identifiers(template))
    missing = sorted(needed - set(variables))
    if missing:
        return None, None, (
            "implementation template uses undefined variables: "
            + ", ".join(missing)
        )
    issue_body_path: str | None = None
    try:
        if "issue_body_file" in needed:
            fd, issue_body_path = tempfile.mkstemp(
                prefix="issuesmith-issue-", suffix=".md"
            )
            os.close(fd)
            Path(issue_body_path).write_text(body, encoding="utf-8")
            variables["issue_body_file"] = issue_body_path
        order = template.substitute(variables)
    except Exception as exc:
        return None, issue_body_path, str(exc)
    return order, issue_body_path, None


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    """Invoke the implementation engine with the same guards as run-guarded --requires-step."""
    context = _context_from_step(ctx)
    violations = pre_llm_violations(step, context)
    if violations:
        summary = get_config().language.message(
            "engine.pre_llm_gate_violation",
            step="implement",
            messages="; ".join(v.message for v in violations),
        )
        return StepResult(
            status="andon",
            andon=Andon(
                kind="decision",
                summary=summary,
                options=["split", "reject"],
            ),
        )

    issue = int(ctx.issue_number)
    body = str(get_forge().issue_get(issue, fields=["body"]).get("body") or "")
    worktree = Path(ctx.worktree_path)
    order, issue_body_path, render_err = _render_order(ctx, step, body)
    if render_err is not None:
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=render_err),
        )
    assert order is not None
    template_name = step.template or "implement.md"
    try:
        run_result = run_order(
            "implementation",
            order,
            cwd=str(worktree),
            template=template_name,
        )
    finally:
        if issue_body_path is not None:
            path = Path(issue_body_path)
            if path.is_file():
                path.unlink()

    if run_result.reported:
        reported = ", ".join(run_result.reported)
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                summary=f"implementation order reported {reported}",
            ),
        )

    commits = _commits_since_base(worktree, ctx.base_branch)
    if run_result.returncode != 0 and commits < 1:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="broken",
                summary=f"implementation engine exited {run_result.returncode}",
            ),
        )
    if run_result.returncode == 0 and commits < 1:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="blocked",
                summary="implementation produced no commit",
            ),
        )
    return StepResult(status="done", markers=["IMPL_DONE"])
