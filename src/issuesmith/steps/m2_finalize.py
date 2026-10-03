"""Deprecated compat re-exports — import from issuesmith.ac_contract instead (#4275).

Kept so existing consumers that still import the old M2 step names keep working
until they switch to the public ``issuesmith.ac_contract`` / ``issuesmith.merge``
APIs. Underscore helpers keep their old signatures; materialize/cleanup delegate
to ``issuesmith.ac_contract``. ``run`` remains the workflow orchestration entry.
"""

from __future__ import annotations

import re
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

from ghdag.forge import ForgePort, get_forge
from ghdag.workflow.state_machine import _load_workflow_config, transition

from issuesmith.ac_contract import (
    GateMaterializationError,
    extract_contract_from_body,
    run_checks,
)
from issuesmith.ac_contract import (
    cleanup_gate_root as _ac_cleanup_gate_root,
)
from issuesmith.ac_contract import (
    materialize_gate_root as _ac_materialize_gate_root,
)
from issuesmith.config import StepConfig, get_config
from issuesmith.engine import run_guarded
from issuesmith.m2_gate import check_gate, synthesize_contract_failures
from issuesmith.ops.labels import run_hygiene as run_label_hygiene
from issuesmith.steps.base import StepContext, StepResult

__all__ = [
    "GateMaterializationError",
    "check_gate",
    "extract_contract_from_body",
    "run",
    "run_checks",
]

warnings.warn(
    "issuesmith.steps.m2_finalize is deprecated; use issuesmith.ac_contract instead",
    DeprecationWarning,
    stacklevel=2,
)


def _github_client() -> ForgePort:
    return get_forge()


def _repo_root() -> Path:
    return get_config().root


def _workflow_path() -> Path:
    return get_config().paths.workflow


def _run_label_hygiene(issue_number: int) -> int:
    code, _result = run_label_hygiene(issue_number, dry_run=False)
    if code != 0:
        print("WARN: label hygiene failed (continuing)", file=sys.stderr)
    return code


def _label_names(client: ForgePort, issue_number: int) -> list[str]:
    data = client.issue_get(issue_number, fields=["labels"])
    return [label["name"] for label in data.get("labels", [])]


def _git(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def _materialize_gate_root(repo_cwd: Path, base_branch: str, issue_number: int, prefix: str) -> Path:
    return _ac_materialize_gate_root(
        repo_cwd, base_branch, f"m2-gate-{issue_number}-{prefix}"
    )


def _cleanup_gate_root(repo_cwd: Path, gate_root: Path) -> None:
    _ac_cleanup_gate_root(repo_cwd, gate_root)


def _evaluate_dual_root(
    body: str,
    labels: list[str],
    nexus_root: Path,
    target_root: Path,
) -> dict[str, Any]:
    base = check_gate(body, labels, repo_root=nexus_root)
    if base["action"] == "migrate":
        return base
    if base["action"] == "retry" and not base.get("contract_failures"):
        return base

    contract = extract_contract_from_body(body)
    if contract is None:
        return base

    try:
        records_by_root = {
            "nexus": run_checks(contract, nexus_root),
            "target": run_checks(contract, target_root),
        }
    except Exception as exc:
        print(f"[m2-gate] dual-root contract check skipped (fail-open): {exc}", file=sys.stderr)
        return base
    failures = synthesize_contract_failures(records_by_root)
    result = dict(base)
    result["contract_failures"] = failures
    result["action"] = "proceed" if not failures else "retry"
    return result


def _run_gate(ctx: StepContext, client: ForgePort) -> dict[str, Any]:
    issue_number = int(ctx.issue_number)
    data = client.issue_get(issue_number, fields=["body", "labels"])
    body = data["body"]
    labels = [label["name"] for label in data["labels"]]
    base_branch = ctx.base_branch
    repo_root = _repo_root()

    if ctx.is_cross_repo == "true":
        target_repo = Path(ctx.target_clone_path)
        if not target_repo.is_absolute():
            target_repo = repo_root / target_repo
        nexus_gate_root = _materialize_gate_root(repo_root, base_branch, issue_number, "nexus-")
        try:
            target_gate_root = _materialize_gate_root(target_repo, base_branch, issue_number, "target-")
        except GateMaterializationError:
            _cleanup_gate_root(repo_root, nexus_gate_root)
            raise
        try:
            return _evaluate_dual_root(body, labels, nexus_gate_root, target_gate_root)
        finally:
            _cleanup_gate_root(repo_root, nexus_gate_root)
            _cleanup_gate_root(target_repo, target_gate_root)

    gate_root = _materialize_gate_root(repo_root, base_branch, issue_number, "")
    try:
        return check_gate(body, labels, repo_root=gate_root)
    finally:
        _cleanup_gate_root(repo_root, gate_root)


def _transition(issue_number: int, target: str) -> None:
    workflow = _load_workflow_config(_workflow_path())
    transition(
        issue_number,
        target,
        workflow.transitions or {},
        workflow.reset_label,
    )


def _fail(reason: str) -> StepResult:
    print(f"REASON: {reason}")
    print(f"REASON: {reason}", file=sys.stderr)
    return StepResult(exit_code=1, pipeline_status="MERGE_FAILED")


def _handle_migrate(
    ctx: StepContext,
    client: ForgePort,
    labels: list[str],
) -> StepResult:
    issue_number = int(ctx.issue_number)
    client.issue_comment(issue_number, get_config().language.message("m2_finalize.migrate"))
    label_set = set(labels)
    if "issuesmith:migrate-ready" in label_set:
        print("FINALIZER: issuesmith:migrate-ready already present (noop)")
    elif "issuesmith:merge-running" in label_set:
        try:
            _transition(issue_number, "issuesmith:migrate-ready")
        except ValueError as exc:
            return _fail(f"finalizer failed to transition to issuesmith:migrate-ready (labels={labels}): {exc}")
        print(f"FINALIZER: transitioned issue {issue_number} to issuesmith:migrate-ready")
    else:
        return _fail(f"MIGRATION_REQUIRED requires merge-running or migrate-ready (labels={labels})")
    return StepResult(exit_code=0, pipeline_status="MIGRATION_REQUIRED")


def _retry_body(
    ctx: StepContext,
    labels: list[str],
    contract_failures: list[str],
) -> str:
    pack = get_config().language
    if contract_failures:
        detail = pack.message(
            "m2_finalize.contract_failed_detail", failures="\n".join(contract_failures)
        )
        step1 = pack.message("m2_finalize.contract_failed_step1", base_branch=ctx.base_branch)
    else:
        detail = pack.message("m2_finalize.unchecked_detail")
        step1 = pack.message("m2_finalize.unchecked_step1")

    key = "m2_finalize.retry_impl" if "issuesmith:develop-running" in labels else "m2_finalize.retry"
    return pack.message(key, detail=detail, step1=step1, issue=ctx.issue_number)


def _handle_retry(
    ctx: StepContext,
    client: ForgePort,
    labels: list[str],
    contract_failures: list[str],
) -> StepResult:
    issue_number = int(ctx.issue_number)
    recovery = _retry_body(ctx, labels, contract_failures)
    label_set = set(labels)

    if "issuesmith:develop-running" in label_set:
        try:
            _transition(issue_number, "issuesmith:develop-done")
        except ValueError as exc:
            return _fail(
                f"finalizer failed to transition to issuesmith:develop-done after gate retry (labels={labels}): {exc}"
            )
        print(f"FINALIZER: transitioned issue {issue_number} to issuesmith:develop-done after gate retry")
    elif "issuesmith:merge-running" in label_set:
        client.issue_update(issue_number, labels_remove=["issuesmith:merge-running"])
        print("FINALIZER: removed issuesmith:merge-running after gate retry")
    elif "issuesmith:develop-done" in label_set:
        print("FINALIZER: issuesmith:develop-done already present after gate retry (noop)")
    else:
        return _fail(
            f"gate retry requires develop-running/merge-running/develop-done (labels={labels})"
        )

    reason = (
        f"acceptance gate not satisfied; blocking downstream "
        f"(labels={labels} contract_failures={contract_failures})"
    )
    print(f"REASON: {reason}")
    print(f"REASON: {reason}", file=sys.stderr)
    return StepResult(exit_code=1, pipeline_status="MERGE_FAILED", recovery=recovery)


def _run_guarded_compaction(ctx: StepContext, template_name: str) -> int:
    template = str(get_config().paths.template_dir / template_name)
    variables = [
        f"issue_number={ctx.issue_number}",
        f"base_branch={ctx.base_branch}",
        f"handler_name={ctx.handler_name}",
        f"m1_result_filename={ctx.m1_result_filename}",
        f"m1r_result_filename={ctx.m1r_result_filename}",
        f"source={ctx.source}",
        f"workflow_name={ctx.workflow_name}",
    ]
    return run_guarded(
        "implementation",
        template,
        variables,
        success_statuses=[],
        failure_status="COMPACT_FAILED",
        emit_status="COMPACT_DONE",
    )


def _list_worktrees(repo_cwd: Path) -> list[Path]:
    proc = _git(["git", "worktree", "list", "--porcelain"], cwd=repo_cwd)
    if proc.returncode != 0:
        return []
    worktrees: list[Path] = []
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            worktrees.append(Path(line.split(" ", 1)[1]))
    return worktrees


def _remove_worktree(repo_cwd: Path, worktree: Path) -> None:
    removed = _git(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_cwd)
    if removed.returncode == 0:
        print(f"CLEANUP: removed worktree {worktree}")
    else:
        print(f"CLEANUP: skip worktree {worktree}")


def _cleanup_branches(repo_cwd: Path, issue_number: str, *, external: bool = False) -> None:
    proc = _git(
        [
            "git",
            "branch",
            "--list",
            f"feat/issue-{issue_number}-*",
            f"docs/issue-{issue_number}-*",
            f"issuesmith/issue-{issue_number}-*",
        ],
        cwd=repo_cwd,
    )
    if proc.returncode != 0:
        return
    prefix = "external " if external else ""
    for line in proc.stdout.splitlines():
        branch = line.lstrip("* ").strip()
        if not branch:
            continue
        deleted = _git(["git", "branch", "-D", branch], cwd=repo_cwd)
        if deleted.returncode == 0:
            print(f"CLEANUP: removed {prefix}branch {branch}")


def _cleanup_worktrees(ctx: StepContext) -> None:
    issue_number = ctx.issue_number
    repo_root = _repo_root()
    pattern = re.compile(rf"/\.claude/worktrees/issue-{issue_number}(-|/|$)")
    for worktree in _list_worktrees(repo_root):
        if pattern.search(str(worktree)):
            _remove_worktree(repo_root, worktree)
    _cleanup_branches(repo_root, issue_number)

    if ctx.is_cross_repo != "true":
        return
    target_repo = Path(ctx.target_clone_path)
    if not target_repo.is_absolute():
        target_repo = repo_root / target_repo
    if not (target_repo / ".git").exists():
        return
    ext_pattern = re.compile(rf"/worktrees/issue-{issue_number}(-|/|$)")
    for worktree in _list_worktrees(target_repo):
        if ext_pattern.search(str(worktree)):
            _remove_worktree(target_repo, worktree)
            print(f"CLEANUP: removed external worktree {worktree}")
    proc = _git(["git", "branch", "--list", f"feat/issue-{issue_number}-*"], cwd=target_repo)
    if proc.returncode == 0:
        for line in proc.stdout.splitlines():
            branch = line.lstrip("* ").strip()
            if branch:
                deleted = _git(["git", "branch", "-D", branch], cwd=target_repo)
                if deleted.returncode == 0:
                    print(f"CLEANUP: removed external branch {branch}")


def _finalize_merge_done(
    ctx: StepContext,
    client: ForgePort,
    labels: list[str],
) -> StepResult | None:
    issue_number = int(ctx.issue_number)
    label_set = set(labels)
    if "issuesmith:merge-done" in label_set:
        print("FINALIZER: issuesmith:merge-done already present (noop)")
        return None
    if not (
        "issuesmith:develop-running" in label_set
        or "issuesmith:develop-done" in label_set
        or "issuesmith:merge-running" in label_set
        or "issuesmith:merge-ready" in label_set
    ):
        return _fail(
            "MERGE_DONE requires develop-running/develop-done/merge-running/merge-ready "
            f"or merge-done (labels={labels})"
        )
    if "issuesmith:develop-running" in label_set:
        try:
            _transition(issue_number, "issuesmith:develop-done")
        except ValueError as exc:
            return _fail(f"finalizer failed to transition to issuesmith:develop-done (labels={labels}): {exc}")
        print(f"FINALIZER: transitioned issue {issue_number} to issuesmith:develop-done")
    try:
        _transition(issue_number, "issuesmith:merge-done")
    except ValueError as exc:
        removable: list[str] = [
            name
            for name in (
                "issuesmith:develop-done",
                "issuesmith:merge-running",
                "issuesmith:merge-ready",
            )
            if name in label_set
        ]
        if not removable:
            return _fail(
                f"finalizer failed to transition to issuesmith:merge-done (labels={labels}): {exc}"
            )
        client.issue_update(
            issue_number,
            labels_remove=removable,
            labels_add=["issuesmith:merge-done"],
        )
        print(
            f"FINALIZER: fallback label swap → issuesmith:merge-done "
            f"(removed={removable}; transition error: {exc})"
        )
        return None
    print(f"FINALIZER: transitioned issue {issue_number} to issuesmith:merge-done")
    return None


def _close_issue_if_open(client: ForgePort, issue_number: int) -> None:
    state = client.issue_get(issue_number, fields=["state"])["state"]
    if state == "OPEN":
        client.issue_close(issue_number)
        print(f"FINALIZER: closed issue {issue_number}")
    else:
        print(f"FINALIZER: issue {issue_number} already {state} (noop close)")


def _resolve_compaction_template(step: StepConfig | None) -> str:
    if step is not None and step.template:
        return step.template
    cfg_step = get_config().steps.get("m2-role-dispatch")
    if cfg_step is None or not cfg_step.template:
        raise ValueError("steps.m2-role-dispatch.template is required for compaction")
    return cfg_step.template


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute the M2 finalize step."""
    issue_number = int(ctx.issue_number)
    client = _github_client()
    _run_label_hygiene(issue_number)
    labels = _label_names(client, issue_number)

    try:
        gate_result = _run_gate(ctx, client)
    except GateMaterializationError as exc:
        return _fail(str(exc))

    action = gate_result["action"]
    contract_failures = gate_result.get("contract_failures") or []
    print(f"GATE: action={action}")

    if action == "migrate":
        return _handle_migrate(ctx, client, labels)
    if action == "retry":
        return _handle_retry(ctx, client, labels, contract_failures)

    if ctx.source:
        rc = _run_guarded_compaction(ctx, _resolve_compaction_template(step))
        if rc != 0:
            client.issue_comment(
                issue_number,
                get_config().language.message(
                    "m2_finalize.compaction_failed", rc=rc, source=ctx.source
                ),
            )
            print(f"COMPACTION: failed rc={rc} (non-blocking)")
        else:
            print("COMPACTION: done")
    else:
        print("COMPACTION: skipped (no source specified)")

    _cleanup_worktrees(ctx)

    finalize_error = _finalize_merge_done(ctx, client, labels)
    if finalize_error is not None:
        return finalize_error

    _close_issue_if_open(client, issue_number)
    return StepResult(exit_code=0, pipeline_status="MERGE_DONE")
