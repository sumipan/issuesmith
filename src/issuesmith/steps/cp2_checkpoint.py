"""CP2 checkpoint Python step — replaces cp2-conditional.md bash (#3162 / #3060)."""

from __future__ import annotations

import subprocess
import sys
import urllib.parse
from pathlib import Path

from ghdag.forge import ForgePort, get_forge
from ghdag.workflow.gates import Violation
from ghdag.workflow.state_machine import _load_workflow_config, transition

from issuesmith.config import StepConfig, get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.cp2_tier import determine_cp2_tier
from issuesmith.engine import resolve, run_guarded
from issuesmith.pr_scope import check_pr_diff_scope, filenames_from_pr_files
from issuesmith.steps.base import StepContext, StepResult

_DEFAULT_TEMPLATE = "_cp2-checkpoint-order.md"
_DIFF_LINES_FALLBACK = 9999

_FAIL_COMMENT = (
    "## CP2 FAIL: 後続ステップをブロックしました\n\n"
    "cp2 が非ゼロ終了したため、m1 / m1r / m2 は DAG 依存失敗（`DEP_FAILED`）でスキップされます。\n\n"
    "ラベルを `issuesmith:develop-done` に差し戻しました。修正後に "
    "`python3 -m issuesmith redispatch {issue} --phase develop` を実行して再投入してください。"
)

_SCOPE_FAIL_COMMENT = (
    "## CP2 FAIL: PR diff scope 違反（pr_diff_scope）\n\n"
    "PR の変更ファイルが `allow_paths` 外、または常時禁止パスに該当します。"
    "後続の m1 / m1r / m2 は `DEP_FAILED` でスキップされます。\n\n"
    "### 違反ファイル\n\n"
    "{violations}\n\n"
    "### 復旧手順\n\n"
    "```bash\n"
    "{recovery}\n"
    "git commit -m \"chore: remove out-of-scope / forbidden paths from PR\"\n"
    "git push\n"
    "```\n\n"
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
    repo = (ctx.target_repo or ctx.issue_repo or "").strip()
    if not repo:
        repo = (get_config().repo or "").strip()
    return repo


def _head_param(repo: str, branch: str) -> str | None:
    branch = branch.strip()
    if not branch:
        return None
    if ":" in branch:
        return branch
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    return f"{owner}:{branch}" if owner else None


def _pulls_list_path(repo: str, branch: str) -> str | None:
    head = _head_param(repo, branch)
    if head is None:
        return None
    encoded = urllib.parse.quote(head, safe="")
    if repo:
        return f"repos/{repo}/pulls?head={encoded}&state=open"
    return f"pulls?head={encoded}&state=open"


def _open_pr_number(client: ForgePort, repo: str, branch: str) -> int | None:
    """Return the open PR number for ``branch``, or None if absent / unreadable."""
    if not branch.strip():
        return None
    path = _pulls_list_path(repo, branch)
    if path is None:
        print(
            "CP2: skip PR search (no owner for head filter; "
            f"repo={repo!r} branch={branch!r})",
            file=sys.stderr,
        )
        return None
    try:
        listed = client.api_request(path)
    except Exception as exc:
        print(f"CP2: PR list failed ({exc})", file=sys.stderr)
        return None
    if not isinstance(listed, list) or not listed:
        return None
    first = next(
        (
            pr
            for pr in listed
            if isinstance(pr, dict)
            and isinstance(pr.get("head"), dict)
            and pr["head"].get("ref") == branch
        ),
        None,
    )
    if first is None or not isinstance(first.get("number"), int):
        return None
    return first["number"]


def _pr_get_detail(client: ForgePort, repo: str, number: int) -> dict | None:
    """Fetch PR detail via ``pr_get`` (includes ``files``, additions, deletions)."""
    try:
        detail = client.pr_get(number, repo=repo or None)
    except Exception as exc:
        print(f"CP2: pr_get({number}) failed ({exc})", file=sys.stderr)
        return None
    if not isinstance(detail, dict):
        return None
    return detail


def _diff_lines_from_detail(detail: dict | None) -> int:
    if detail is None:
        return _DIFF_LINES_FALLBACK
    additions = detail.get("additions")
    deletions = detail.get("deletions")
    if additions is None and deletions is None:
        return _DIFF_LINES_FALLBACK
    return int(additions or 0) + int(deletions or 0)


def _pr_diff_lines(client: ForgePort, repo: str, branch: str) -> int:
    """Return additions+deletions for the open PR on ``branch``, or 9999 if absent.

    Uses list + ``pr_get`` so callers that only need line counts stay compatible.
    Prefer ``_load_pr_for_branch`` in ``run()`` to avoid a duplicate ``pr_get``.
    """
    number = _open_pr_number(client, repo, branch)
    if number is None:
        return _DIFF_LINES_FALLBACK
    return _diff_lines_from_detail(_pr_get_detail(client, repo, number))


def _load_pr_for_branch(
    client: ForgePort, repo: str, branch: str
) -> tuple[int, dict | None]:
    """Return (diff_lines, pr_get detail or None). Single pr_get when PR exists."""
    number = _open_pr_number(client, repo, branch)
    if number is None:
        return _DIFF_LINES_FALLBACK, None
    detail = _pr_get_detail(client, repo, number)
    return _diff_lines_from_detail(detail), detail


def _allow_paths_from_body(body: str) -> list[str]:
    try:
        meta = parse_issue_metadata(body)
    except ValueError:
        return []
    raw = meta.get("allow_paths", [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if p is not None and str(p).strip()]


def _format_scope_comment(issue_number: int, violations: list[Violation]) -> str:
    lines = [f"- `{v.location or '?'}`: {v.message}" for v in violations]
    recovery_cmds = []
    for v in violations:
        path = v.location or ""
        if path:
            recovery_cmds.append(f"git checkout main -- {path}")
    recovery = "\n".join(recovery_cmds) if recovery_cmds else "git checkout main -- <path>"
    return _SCOPE_FAIL_COMMENT.format(
        issue=issue_number,
        violations="\n".join(lines),
        recovery=recovery,
    )


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


def _handle_fail(
    client: ForgePort,
    issue_number: int,
    *,
    comment: str | None = None,
) -> StepResult:
    body = comment if comment is not None else _FAIL_COMMENT.format(issue=issue_number)
    client.issue_comment(issue_number, body)
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


def _check_pr_scope(
    client: ForgePort,
    issue_number: int,
    body: str,
    pr_detail: dict | None,
) -> StepResult | None:
    """Run pr_diff_scope gate. Return a FAIL StepResult, or None to continue."""
    if pr_detail is None:
        return None
    file_entries = pr_detail.get("files")
    filenames = filenames_from_pr_files(file_entries)
    if not filenames:
        return None
    allow_paths = _allow_paths_from_body(body)
    violations = check_pr_diff_scope(
        filenames, allow_paths, file_entries=file_entries
    )
    if not violations:
        return None
    print(
        f"CP2: pr_diff_scope violations ({len(violations)}): "
        + ", ".join(v.location or "?" for v in violations),
        file=sys.stderr,
    )
    return _handle_fail(
        client,
        issue_number,
        comment=_format_scope_comment(issue_number, violations),
    )


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute the CP2 checkpoint step."""
    issue_number = int(ctx.issue_number)
    client = _github_client()
    repo = _resolve_repo(ctx)
    repo_root = _repo_root()

    try:
        body = client.issue_get(issue_number, fields=["body"]).get("body") or ""
    except Exception:
        body = ""

    diff_lines, pr_detail = _load_pr_for_branch(client, repo, ctx.branch)
    scope_fail = _check_pr_scope(client, issue_number, body, pr_detail)
    if scope_fail is not None:
        return scope_fail

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
