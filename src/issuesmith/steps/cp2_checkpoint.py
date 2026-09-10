"""CP2 checkpoint Python step — replaces cp2-conditional.md bash (#3162 / #3060)."""

from __future__ import annotations

import subprocess
import sys
import urllib.parse
from pathlib import Path

from ghdag.forge import ForgePort, get_forge
from ghdag.workflow.state_machine import _load_workflow_config, transition

from issuesmith.config import StepConfig, get_config
from issuesmith.cp2_tier import determine_cp2_tier
from issuesmith.engine import resolve, run_guarded
from issuesmith.steps.base import StepContext, StepResult

_DEFAULT_TEMPLATE = "_cp2-checkpoint-order.md"
_DIFF_LINES_FALLBACK = 9999

_FAIL_COMMENT = (
    "## CP2 FAIL: 後続ステップをブロックしました\n\n"
    "cp2 が非ゼロ終了したため、m1 / m1r / m2 は DAG 依存失敗（`DEP_FAILED`）でスキップされます。\n\n"
    "ラベルを `issuesmith:develop-done` に差し戻しました。修正後に "
    "`python3 -m issuesmith redispatch {issue} --phase develop` を実行して再投入してください。"
)


def _github_client() -> ForgePort:
    return get_forge()


def _repo_root() -> Path:
    return get_config().root


def _workflow_path() -> Path:
    return get_config().paths.workflow


def _resolve_repo(ctx: StepContext) -> str:
    return (ctx.target_repo or ctx.issue_repo or "").strip()


def _head_param(repo: str, branch: str) -> str:
    branch = branch.strip()
    if not branch:
        return ""
    if ":" in branch:
        return branch
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    return f"{owner}:{branch}" if owner else branch


def _pulls_list_path(repo: str, branch: str) -> str:
    head = _head_param(repo, branch)
    encoded = urllib.parse.quote(head, safe="")
    if repo:
        return f"repos/{repo}/pulls?head={encoded}&state=open"
    return f"pulls?head={encoded}&state=open"


def _pr_diff_lines(client: ForgePort, repo: str, branch: str) -> int:
    """Return additions+deletions for the open PR on ``branch``, or 9999 if absent.

    Real GitHub list payloads omit additions/deletions (null); detail GET is required.
    ``api_request`` has no ``params=`` kwarg — head filter is embedded in the path
    (equivalent to the design's ``params={"head": branch}``).
    """
    if not branch.strip():
        return _DIFF_LINES_FALLBACK
    try:
        listed = client.api_request(_pulls_list_path(repo, branch))
    except Exception as exc:
        print(f"CP2: PR list failed ({exc}); diff_lines={_DIFF_LINES_FALLBACK}", file=sys.stderr)
        return _DIFF_LINES_FALLBACK
    if not isinstance(listed, list) or not listed:
        return _DIFF_LINES_FALLBACK
    first = listed[0]
    if not isinstance(first, dict) or not isinstance(first.get("number"), int):
        return _DIFF_LINES_FALLBACK
    number = first["number"]
    detail_path = f"repos/{repo}/pulls/{number}" if repo else f"pulls/{number}"
    try:
        detail = client.api_request(detail_path)
    except Exception as exc:
        print(f"CP2: PR detail failed ({exc}); diff_lines={_DIFF_LINES_FALLBACK}", file=sys.stderr)
        return _DIFF_LINES_FALLBACK
    if not isinstance(detail, dict):
        return _DIFF_LINES_FALLBACK
    additions = detail.get("additions")
    deletions = detail.get("deletions")
    if additions is None and deletions is None:
        return _DIFF_LINES_FALLBACK
    return int(additions or 0) + int(deletions or 0)


def _unchecked_ac_count(body: str) -> int:
    in_ac = False
    count = 0
    for line in body.split("\n"):
        s = line.strip()
        if s.startswith("## 受け入れ条件"):
            in_ac = True
            continue
        if s.startswith("## ") and in_ac:
            in_ac = False
        if in_ac and s.startswith("- [ ]"):
            count += 1
    return count


def _p2_all_pass(repo_root: Path, p2_result_filename: str) -> bool:
    if not p2_result_filename:
        return False
    path = repo_root / "jobs" / p2_result_filename
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "VERIFY_FAILED_CHECKS: (none)" in text


def _tier_via_cli(diff_lines: int, unchecked_ac: int, p2_all_pass: bool) -> str:
    """Call ``python3 -m issuesmith tier cp2`` (design); fall back to in-process."""
    proc = subprocess.run(
        [
            "python3",
            "-m",
            "issuesmith",
            "tier",
            "cp2",
            "--diff-lines",
            str(diff_lines),
            "--unchecked-ac",
            str(unchecked_ac),
            "--p2-all-pass",
            "true" if p2_all_pass else "false",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        tier = (proc.stdout or "").strip().splitlines()[-1].strip() if proc.stdout else ""
        if tier in ("light", "heavy"):
            return tier
    return determine_cp2_tier(diff_lines, unchecked_ac, p2_all_pass)


def _execution_constraints() -> str:
    try:
        from tools._core.execution_constraints import BLOCK  # type: ignore[import-not-found]

        return str(BLOCK)
    except Exception:
        return ""


def _resolve_template(step: StepConfig | None) -> str:
    if step is not None and step.template:
        return step.template
    cfg_step = get_config().steps.get("cp2")
    if cfg_step is not None and cfg_step.template:
        return cfg_step.template
    return _DEFAULT_TEMPLATE


def _run_guarded_design(ctx: StepContext, tier: str, template_name: str) -> int:
    """Run design order via ``run_guarded`` (m2_finalize ``_run_guarded_compaction`` pattern)."""
    template = str(get_config().paths.template_dir / template_name)
    selection = resolve("design", tier)
    variables = [
        f"issue_number={ctx.issue_number}",
        f"is_cross_repo={ctx.is_cross_repo}",
        f"worktree_path={ctx.worktree_path}",
        f"target_worktree_path={ctx.target_worktree_path}",
        f"branch={ctx.branch}",
        f"base_branch={ctx.base_branch}",
        f"p1_result_filename={ctx.p1_result_filename}",
        f"p2_result_filename={ctx.p2_result_filename}",
        f"p3_result_filename={ctx.p3_result_filename}",
        f"target_repo={ctx.target_repo}",
        f"allow_paths={ctx.allow_paths}",
        f"execution_constraints={_execution_constraints()}",
        f"model={selection.model}",
    ]
    return run_guarded(
        "design",
        template,
        variables,
        success_statuses=["CP2_PASS", "CP2_SKIPPED"],
        failure_status="CP2_FAILED",
        tier=tier,
    )


def _label_names(client: ForgePort, issue_number: int) -> list[str]:
    data = client.issue_get(issue_number, fields=["labels"])
    return [label["name"] for label in data.get("labels", [])]


def _transition(issue_number: int, target: str) -> None:
    workflow = _load_workflow_config(_workflow_path())
    transition(
        issue_number,
        target,
        workflow.transitions or {},
        workflow.reset_label,
    )


def _handle_fail(client: ForgePort, issue_number: int) -> StepResult:
    client.issue_comment(issue_number, _FAIL_COMMENT.format(issue=issue_number))
    labels = _label_names(client, issue_number)
    if "issuesmith:develop-done" in labels:
        print("CP2 FAIL handler: issuesmith:develop-done already present (noop transition)")
    else:
        try:
            _transition(issue_number, "issuesmith:develop-done")
            print(f"CP2 FAIL handler: transitioned issue {issue_number} to issuesmith:develop-done")
        except ValueError as exc:
            print(f"CP2 FAIL handler: transition failed: {exc}", file=sys.stderr)
    return StepResult(exit_code=1, pipeline_status="CP2_FAILED")


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute the CP2 checkpoint step."""
    issue_number = int(ctx.issue_number)
    client = _github_client()
    repo = _resolve_repo(ctx)
    repo_root = _repo_root()

    diff_lines = _pr_diff_lines(client, repo, ctx.branch)

    try:
        body = client.issue_get(issue_number, fields=["body"]).get("body") or ""
    except Exception:
        body = ""
    unchecked_ac = _unchecked_ac_count(body)
    p2_pass = _p2_all_pass(repo_root, ctx.p2_result_filename)
    tier = _tier_via_cli(diff_lines, unchecked_ac, p2_pass)

    selection = resolve("design", tier)
    print(
        f"CP2 design: {selection.engine}\t{selection.model} tier={tier} "
        f"diff_lines={diff_lines} unchecked_ac={unchecked_ac} p2_all_pass={p2_pass}"
    )

    try:
        rc = _run_guarded_design(ctx, tier, _resolve_template(step))
    except Exception as exc:
        # Mirror bash REJECTED short-circuit: no develop-done rollback on refuse.
        print(f"REJECTED:{exc}", file=sys.stderr)
        return StepResult(exit_code=1, pipeline_status="CP2_FAILED")

    if rc == 0:
        return StepResult(exit_code=0, pipeline_status="")
    return _handle_fail(client, issue_number)
