"""M2 finalize Python step — replaces m2-role-dispatch.md bash (#2869)."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from ghdag.github_client import GitHubClient
from ghdag.workflow.state_machine import _load_workflow_config, transition

from issuesmith.ac_contract import extract_contract_from_body, run_checks
from issuesmith.config import get_config
from issuesmith.engine import run_guarded
from issuesmith.m2_gate import check_gate, synthesize_contract_failures
from issuesmith.ops.label_hygiene import run as run_label_hygiene
from issuesmith.steps.base import StepContext, StepResult


class GateMaterializationError(RuntimeError):
    """origin/base の一時 worktree を作れなかった。"""


def _github_client() -> GitHubClient:
    return GitHubClient()


def _repo_root() -> Path:
    return get_config().root


def _workflow_path() -> Path:
    return get_config().paths.workflow


def _run_label_hygiene(issue_number: int) -> int:
    code, _result = run_label_hygiene(issue_number, dry_run=False)
    if code != 0:
        print("WARN: label hygiene failed (continuing)", file=sys.stderr)
    return code


def _label_names(client: GitHubClient, issue_number: int) -> list[str]:
    data = client.issue_get(issue_number, fields=["labels"])
    return [label["name"] for label in data.get("labels", [])]


def _git(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def _materialize_gate_root(repo_cwd: Path, base_branch: str, issue_number: int, prefix: str) -> Path:
    gate_root = Path(tempfile.mkdtemp(prefix=f"m2-gate-{issue_number}-{prefix}"))
    fetch = _git(["git", "fetch", "-q", "origin", base_branch], cwd=repo_cwd)
    add = _git(
        ["git", "worktree", "add", "--detach", "-q", str(gate_root), f"origin/{base_branch}"],
        cwd=repo_cwd,
    )
    if fetch.returncode != 0 or add.returncode != 0:
        shutil.rmtree(gate_root, ignore_errors=True)
        repo = str(repo_cwd)
        raise GateMaterializationError(
            f"could not materialize origin/{base_branch} for contract check (repo={repo})"
        )
    return gate_root


def _cleanup_gate_root(repo_cwd: Path, gate_root: Path) -> None:
    remove = _git(["git", "worktree", "remove", "--force", str(gate_root)], cwd=repo_cwd)
    if remove.returncode != 0:
        shutil.rmtree(gate_root, ignore_errors=True)


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

    records_by_root = {
        "nexus": run_checks(contract, nexus_root),
        "target": run_checks(contract, target_root),
    }
    failures = synthesize_contract_failures(records_by_root)
    result = dict(base)
    result["contract_failures"] = failures
    result["action"] = "proceed" if not failures else "retry"
    return result


def _run_gate(ctx: StepContext, client: GitHubClient) -> dict[str, Any]:
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
        target_gate_root = _materialize_gate_root(target_repo, base_branch, issue_number, "target-")
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
    client: GitHubClient,
    labels: list[str],
) -> StepResult:
    issue_number = int(ctx.issue_number)
    client.issue_comment(
        issue_number,
        "## M2: 受け入れ条件が未完了です\n\n"
        "未チェックの受け入れ条件が残っています。マイグレーション完了後に "
        "`issuesmith:migrate-ready` を付与してください。",
    )
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
    if contract_failures:
        detail = "受け入れ条件 YAML 契約の検証に失敗しました:\n" + "\n".join(contract_failures)
        step1 = (
            f"1. `paths_must_exist` に列挙したファイルが {ctx.base_branch} に実在するよう"
            "修正する（不足ファイルの追加 or 契約の修正）"
        )
    else:
        detail = "未チェックの受け入れ条件が残っています。"
        step1 = "1. 受け入れ条件をすべてチェックする"

    issue_number = ctx.issue_number
    if "issuesmith:develop-running" in labels:
        return (
            f"## M2: 受け入れ条件が未完了です\n\n{detail}\n\n"
            "復旧手順（impl コンテキスト）:\n"
            f"{step1}\n"
            f"2. `python3 -m issuesmith queue enqueue --issue {issue_number} --phase merge "
            "--source recovery --actor-kind human --priority high --requested-by <login> --force` "
            "を実行する\n"
            "   ※ impl の冪等キーは消費済みだが、merge の冪等キーは未使用のため reset 不要"
        )
    return (
        f"## M2: 受け入れ条件が未完了です\n\n{detail}\n\n"
        "復旧手順:\n"
        f"{step1}\n"
        "2. `issuesmith:reset` ラベルを付与してリセットする\n"
        "3. ghdag が次のサイクルでリセットを処理するのを待つ（約30秒）\n"
        f"4. `python3 -m issuesmith queue enqueue --issue {issue_number} --phase merge "
        "--source recovery --actor-kind human --priority high --requested-by <login> --force` "
        "を実行して再投入する"
    )


def _handle_retry(
    ctx: StepContext,
    client: GitHubClient,
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


def _run_guarded_compaction(ctx: StepContext) -> int:
    template = str(get_config().paths.template_dir / "m2-compact.md")
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
    client: GitHubClient,
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
    ):
        return _fail(
            "MERGE_DONE requires develop-running/develop-done/merge-running or merge-done "
            f"(labels={labels})"
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
        return _fail(f"finalizer failed to transition to issuesmith:merge-done (labels={labels}): {exc}")
    print(f"FINALIZER: transitioned issue {issue_number} to issuesmith:merge-done")
    return None


def _close_issue_if_open(client: GitHubClient, issue_number: int) -> None:
    state = client.issue_get(issue_number, fields=["state"])["state"]
    if state == "OPEN":
        client.issue_close(issue_number)
        print(f"FINALIZER: closed issue {issue_number}")
    else:
        print(f"FINALIZER: issue {issue_number} already {state} (noop close)")


def run(ctx: StepContext) -> StepResult:
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
        rc = _run_guarded_compaction(ctx)
        if rc != 0:
            client.issue_comment(
                issue_number,
                "## M2 コンパクション失敗\n\n"
                f"コンパクション LLM ステップが終了コード {rc} で失敗しました"
                "（プロバイダ拒否・タイムアウト等の可能性）。\n"
                "PR マージは完了済みです。source ドキュメントへのコンパクションを手動で行ってください。\n"
                f"対象: `{ctx.source}`",
            )
            print(f"COMPACTION: failed rc={rc} (non-blocking)")
        else:
            print("COMPACTION: done")
    else:
        print("COMPACTION: skipped (source 未指定)")

    _cleanup_worktrees(ctx)

    finalize_error = _finalize_merge_done(ctx, client, labels)
    if finalize_error is not None:
        return finalize_error

    _close_issue_if_open(client, issue_number)
    return StepResult(exit_code=0, pipeline_status="MERGE_DONE")
