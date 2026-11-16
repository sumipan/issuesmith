"""Run the implementation LLM for the reference develop line."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from string import Template

from issuesmith.config import StepConfig, get_config
from issuesmith.contract import Andon, StepContext, StepResult
from issuesmith.forge import get_forge
from issuesmith.verbs import exec_file


def _commits_since_base(worktree: Path, base_branch: str) -> int:
    proc = subprocess.run(
        ["git", "rev-list", "--count", f"origin/{base_branch}..HEAD"],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip() or "0")
    except ValueError:
        return 0


def _render_order(ctx: StepContext, step: StepConfig, body: str) -> str:
    if not step.template:
        raise RuntimeError("implement step has no template configured")
    template_path = get_config().paths.template_dir / step.template
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute(
        issue_number=ctx.issue_number,
        issue_body=body,
        worktree_path=ctx.worktree_path,
        branch=ctx.branch,
        allow_paths=ctx.allow_paths,
        repair_violation_summary=ctx.repair_violation_summary or "",
    )


def run(ctx: StepContext, step: StepConfig) -> StepResult:
    """Invoke the implementation engine and require at least one new commit."""
    forge = get_forge()
    issue = int(ctx.issue_number)
    body = str(forge.issue_get(issue, fields=["body"]).get("body") or "")
    worktree = Path(ctx.worktree_path)
    order = _render_order(ctx, step, body)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="develop-implement-",
        suffix=".md",
        delete=False,
    ) as fh:
        fh.write(order)
        order_path = fh.name
    try:
        rc = exec_file("implementation", order_path, cwd=str(worktree))
    finally:
        Path(order_path).unlink(missing_ok=True)
    if rc != 0:
        return StepResult(
            status="andon",
            andon=Andon(kind="broken", summary=f"implementation engine exited {rc}"),
        )
    if _commits_since_base(worktree, ctx.base_branch) < 1:
        return StepResult(
            status="andon",
            andon=Andon(
                kind="blocked",
                summary="implementation produced no commit",
            ),
        )
    return StepResult(status="done", markers=["IMPL_DONE"])
