"""Deprecated compat re-exports — import from issuesmith.merge instead (#4275).

Kept so existing consumers that still import the old M1 step names keep working
until they switch to the public ``issuesmith.merge`` API. Underscore helpers
keep their old signatures; bodies delegate to ``issuesmith.merge``. ``run``
remains the workflow orchestration entry point for the M1 step.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Any

from ghdag.forge import ForgePort, get_forge

from issuesmith import merge as _merge
from issuesmith.config import StepConfig, get_config
from issuesmith.steps.base import StepContext, StepResult

__all__ = ["run"]

warnings.warn(
    "issuesmith.steps.m1_merge is deprecated; use issuesmith.merge instead",
    DeprecationWarning,
    stacklevel=2,
)

_BACKOFF_SEC = (5, 10, 20)
_MERGE_STATE_ATTEMPTS = 3


def _github_client() -> ForgePort:
    return get_forge()


def _resolve_pr_repo(ctx: StepContext) -> str:
    if ctx.is_cross_repo == "true" and ctx.target_repo.strip():
        return ctx.target_repo.strip()
    return (ctx.issue_repo or ctx.target_repo or "").strip()


def _worktree_path(ctx: StepContext) -> str:
    if ctx.is_cross_repo == "true" and ctx.target_worktree_path.strip():
        return ctx.target_worktree_path.strip()
    return ctx.worktree_path.strip()


def _list_pulls(
    client: ForgePort,
    repo: str,
    *,
    state: str = "open",
    head: str | None = None,
) -> list[dict[str, Any]]:
    return _merge.list_pulls(client, repo, state=state, head=head)


def _find_pr(
    client: ForgePort,
    repo: str,
    branch: str,
    issue_number: int,
) -> tuple[int | None, str, str]:
    result = _merge.find_pr(client, repo, branch, issue_number)
    return result.number, result.state, result.stage


def _is_already_merged(client: ForgePort, repo: str, number: int) -> bool:
    return _merge.is_already_merged(client, repo, number)


def _graphql_merge_state(
    client: ForgePort, repo: str, number: int
) -> dict[str, str]:
    info = _merge.graphql_merge_state(client, repo, number)
    return {
        "mergeStateStatus": info.merge_state_status,
        "mergeable": info.mergeable,
        "state": info.state,
        "headRefName": info.head_ref_name,
    }


def _get_merge_state(client: ForgePort, repo: str, number: int) -> dict[str, str]:
    info = _merge.get_merge_state(client, repo, number)
    return {
        "mergeStateStatus": info.merge_state_status,
        "mergeable": info.mergeable,
        "state": info.state,
        "headRefName": info.head_ref_name,
    }


def _poll_merge_state(client: ForgePort, repo: str, number: int) -> dict[str, str]:
    """Retry while mergeStateStatus is BLOCKED (max 3)."""
    last: dict[str, str] = {
        "mergeStateStatus": "UNKNOWN",
        "mergeable": "UNKNOWN",
        "state": "",
        "headRefName": "",
    }
    for i in range(_MERGE_STATE_ATTEMPTS):
        last = _get_merge_state(client, repo, number)
        status = last.get("mergeStateStatus") or "UNKNOWN"
        print(f"  try {i + 1}: mergeStateStatus={status} mergeable={last.get('mergeable')}")
        if status != "BLOCKED":
            return last
        if i < _MERGE_STATE_ATTEMPTS - 1:
            time.sleep(_BACKOFF_SEC[i])
    return last


def _local_merge_verify(
    worktree: str, base_branch: str, head_ref: str
) -> tuple[str, bool]:
    result = _merge.verify_local_merge(worktree, base_branch, head_ref)
    if result.result == "CLEAN":
        print("LOCAL_VERIFY: CLEAN (no conflicts detected by merge-tree)")
    elif result.result == "CONFLICT":
        print("LOCAL_VERIFY: CONFLICT (merge-tree exit code=1)")
    elif result.result == "SKIPPED" and worktree and Path(worktree).is_dir():
        print("LOCAL_VERIFY: SKIPPED (fetch failed or missing inputs)", file=sys.stderr)
    return result.result, result.verified


def _m2_gate_preflight(body: str, labels: list[str]) -> list[dict[str, Any]]:
    """Run ``python3 -m issuesmith gate-preflight --gate m2``; return violations."""
    with tempfile.TemporaryDirectory(prefix="m1-gate-") as tmp:
        body_path = Path(tmp) / "body.md"
        labels_path = Path(tmp) / "labels.txt"
        body_path.write_text(body, encoding="utf-8")
        labels_path.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")
        proc = subprocess.run(
            [
                "python3",
                "-m",
                "issuesmith",
                "gate-preflight",
                "--gate",
                "m2",
                "--body-file",
                str(body_path),
                "--labels-file",
                str(labels_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        raw = (proc.stdout or "").strip() or "[]"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"gate-preflight parse failed: {raw[:200]!r}", file=sys.stderr)
            return []
        return data if isinstance(data, list) else []


def _version_behind_base_check(worktree: str, base_branch: str, merge_state: str) -> str:
    """Run ``m1.version_behind_base``; return ``OK`` / ``FIXED`` / ``skipped (...)`` / ``FAILED (...)``."""
    from issuesmith.gates.base import ContractInput
    from issuesmith.gates.m1 import VersionBehindBaseGate

    if merge_state != "CLEAN":
        return f"skipped (merge_state={merge_state})"
    if not worktree or not Path(worktree).is_dir():
        return "skipped (worktree not present)"
    gate = VersionBehindBaseGate(Path(worktree), base_branch)
    violations = gate.check("", [])
    if not violations:
        return "OK"
    for v in violations:
        print(f"  [{v.rule_id}] {v.message}")
    try:
        gate.fix(ContractInput(body=""))
    except RuntimeError as exc:
        print(f"version_behind_base fix failed: {exc}", file=sys.stderr)
        return f"FAILED ({exc})"
    return "FIXED"


def _find_companion_pr(
    client: ForgePort, issue_repo: str, branch: str
) -> int | None:
    return _merge.find_companion_pr(
        client, issue_repo, branch, companion_suffix="-diary"
    )


def _companion_ready(
    client: ForgePort, issue_repo: str, number: int
) -> tuple[bool, str, bool]:
    result = _merge.check_companion_ready(client, issue_repo, number)
    return result.ready, result.review_decision, result.ci_ok


def _post_merge_pytest(work_dir: str) -> int:
    if not work_dir or not Path(work_dir).is_dir():
        print("POST_MERGE_WORKTREE: (not present, skipping)")
        return 0
    print(f"POST_MERGE_WORKTREE: {work_dir}")
    result = _merge.run_post_merge_pytest(work_dir)
    return result.exit_code


def _reported(failed_stages: list[str], **extra: Any) -> StepResult:
    print("=== SUMMARY ===")
    for key, value in extra.items():
        print(f"{key}: {value}")
    if failed_stages:
        print(f"MERGE_FAILED_STAGES:{' '.join(failed_stages)}")
    else:
        print("MERGE_FAILED_STAGES: (none)")
    print("PIPELINE_STATUS: MERGE_REPORTED")
    return StepResult(exit_code=0, pipeline_status="MERGE_REPORTED")


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute the M1 merge step. Always exit 0 + MERGE_REPORTED."""
    del step  # reserved for dispatch StepConfig
    failed: list[str] = []
    client = _github_client()
    issue_number = int(ctx.issue_number)
    pr_repo = _resolve_pr_repo(ctx)
    issue_repo = (ctx.issue_repo or pr_repo).strip()

    print(f"=== M1 MERGE REPORT (Issue #{ctx.issue_number}) ===")
    print(f"BRANCH: {ctx.branch}")
    print(f"BASE_BRANCH: {ctx.base_branch}")
    print(f"IS_CROSS_REPO: {ctx.is_cross_repo}")
    print(f"TARGET_REPO: {ctx.target_repo}")
    print("")

    companion_pr: int | None = None

    print("## [companion_pr_check]")
    if ctx.has_diary_changes == "true":
        companion_pr = _find_companion_pr(client, issue_repo, ctx.branch)
        if companion_pr is None:
            print("COMPANION_PR: (not found — skipping check)")
        else:
            print(f"COMPANION_PR_NUMBER: {companion_pr}")
            ready, decision, ci_ok = _companion_ready(client, issue_repo, companion_pr)
            print(f"  companion: #{companion_pr} decision={decision} ci={ci_ok}")
            if not ready:
                print(f"  COMPANION_NOT_READY: #{companion_pr}")
                try:
                    client.issue_comment(
                        issue_number,
                        get_config().language.message(
                            "m1_merge.companion_not_ready", pr=companion_pr, branch=ctx.branch
                        )
                        + "\n\nPIPELINE_STATUS: MERGE_PENDING",
                    )
                except Exception as exc:
                    print(f"companion comment failed: {exc}", file=sys.stderr)
                return _reported(["companion_pr_not_ready"])
            print("COMPANION_CHECK: OK")
    else:
        print("(has_diary_changes != true — skipped)")
    print("")

    print("## [pr_search]")
    pr_number, pr_state, stage = _find_pr(client, pr_repo, ctx.branch, issue_number)
    if pr_number is None:
        print(f"PR_NOT_FOUND (branch: {ctx.branch})")
        return _reported(["pr_search"])
    print(f"PR_NUMBER: {pr_number}")
    print(f"PR_STATE: {pr_state}")
    print(f"PR_SEARCH_STAGE: {stage}")
    print("")

    print("## [already_merged_check]")
    if _is_already_merged(client, pr_repo, pr_number):
        print("PR_MERGED: true")
        print("ALREADY_MERGED: yes")
        print("")
        return _reported([], PR_NUMBER=pr_number)
    print("PR_MERGED: false")
    print("ALREADY_MERGED: no")
    print("")

    print("## [merge_state]")
    merge_info = _poll_merge_state(client, pr_repo, pr_number)
    merge_state = merge_info.get("mergeStateStatus") or "UNKNOWN"
    head_ref = merge_info.get("headRefName") or ctx.branch
    print(f"MERGE_STATE: {merge_state}")
    print(f"MERGEABLE: {merge_info.get('mergeable')}")
    print(f"PR_HEAD_REF: {head_ref}")
    print("")

    print("## [local_merge_verify]")
    local_result = "SKIPPED"
    local_verified = False
    if merge_state == "UNKNOWN":
        local_result, local_verified = _local_merge_verify(
            _worktree_path(ctx), ctx.base_branch, head_ref
        )
        if local_verified:
            merge_state = "CLEAN"
        elif local_result == "CONFLICT":
            failed.append("merge_state")
        else:
            failed.append("merge_state")
    else:
        print(f"LOCAL_VERIFY: SKIPPED (MERGE_STATE={merge_state}, not UNKNOWN)")
    print(f"LOCAL_VERIFIED: {local_verified}")
    print("")

    print("## [gate_preflight_m2]")
    try:
        issue_data = client.issue_get(issue_number, fields=["body", "labels"])
        body = issue_data.get("body") or ""
        labels = [lb["name"] for lb in issue_data.get("labels", [])]
    except Exception as exc:
        print(f"issue_get failed ({exc})", file=sys.stderr)
        body, labels = "", []
    violations = _m2_gate_preflight(body, labels)
    fail_msgs = [
        str(v.get("message") or "")
        for v in violations
        if isinstance(v, dict) and v.get("severity") == "fail"
    ]
    print(f"M2_VIOLATIONS: {json.dumps(violations, ensure_ascii=False)}")
    print(f"M2_FAIL_COUNT: {len(fail_msgs)}")
    if fail_msgs:
        try:
            client.issue_comment(
                issue_number,
                get_config().language.message(
                    "m1_merge.m2_gate_blocked", items="- " + "\n- ".join(fail_msgs)
                )
                + "\n\nPIPELINE_STATUS: MERGE_PENDING",
            )
        except Exception as exc:
            print(f"gate comment failed: {exc}", file=sys.stderr)
        print("GATE_PREFLIGHT_M2: BLOCKED")
        return _reported(["gate_preflight_m2"])
    print("GATE_PREFLIGHT_M2: OK")
    print("")

    print("## [version_behind_base_check]")
    version_status = _version_behind_base_check(_worktree_path(ctx), ctx.base_branch, merge_state)
    print(f"VERSION_BEHIND_BASE: {version_status}")
    if version_status.startswith("FAILED"):
        failed.append("version_behind_base")
        return _reported(failed, PR_NUMBER=pr_number)
    if version_status == "FIXED":
        merge_info = _poll_merge_state(client, pr_repo, pr_number)
        merge_state = merge_info.get("mergeStateStatus") or "UNKNOWN"
        print(f"MERGE_STATE: {merge_state}")
        if merge_state == "UNKNOWN" and "merge_state" not in failed:
            failed.append("merge_state")
    print("")

    print("## [merge_attempt]")
    merge_attempted = False
    merge_rc = -1
    if merge_state == "CLEAN":
        merge_attempted = True
        try:
            client.pr_merge(pr_number, method="merge", delete_branch=True, repo=pr_repo or None)
            merge_rc = 0
            print("MERGE: ok")
        except Exception as exc:
            merge_rc = 1
            print(f"MERGE: failed ({exc})", file=sys.stderr)
            failed.append("merge_attempt")
    else:
        if merge_state == "UNKNOWN":
            print(f"MERGE_SKIPPED: mergeStateStatus=UNKNOWN, local_verify={local_result}")
        else:
            print(f"MERGE_SKIPPED: mergeStateStatus={merge_state} (not CLEAN)")
            if "merge_state" not in failed:
                failed.append("merge_state")
    print(f"MERGE_ATTEMPTED: {'yes' if merge_attempted else 'no'}")
    print(f"MERGE_RC: {merge_rc}")
    print("")

    print("## [companion_pr_merge]")
    companion_merge_rc = -1
    if (
        ctx.has_diary_changes == "true"
        and merge_attempted
        and merge_rc == 0
        and companion_pr is not None
    ):
        try:
            client.pr_merge(
                companion_pr,
                method="merge",
                delete_branch=True,
                repo=issue_repo or None,
            )
            companion_merge_rc = 0
            print(f"COMPANION_MERGE: ok #{companion_pr}")
        except Exception as exc:
            companion_merge_rc = 1
            print(f"COMPANION_MERGE: failed ({exc})", file=sys.stderr)
            failed.append("companion_merge")
    elif companion_pr is None:
        print("COMPANION_MERGE: skipped (no companion PR)")
    else:
        print("COMPANION_MERGE: skipped (primary merge did not succeed or diary off)")
    print(f"COMPANION_MERGE_RC: {companion_merge_rc}")
    print("")

    print("## [post_merge_test]")
    posttest_rc = 0
    if merge_attempted and merge_rc == 0:
        posttest_rc = _post_merge_pytest(_worktree_path(ctx))
        if posttest_rc != 0:
            failed.append("post_merge_test")
            try:
                client.issue_update(issue_number, labels_add=["issuesmith:merge-running"])
                print("LABEL: added issuesmith:merge-running (post_merge_test failed)")
            except Exception as exc:
                print(f"merge-running label failed: {exc}", file=sys.stderr)
    else:
        print("POST_MERGE_TEST: skipped (merge did not run successfully)")
    print(f"POSTTEST_RC: {posttest_rc}")
    print("")

    return _reported(
        failed,
        PR_NUMBER=pr_number,
        MERGE_STATE=merge_state,
        MERGE_RC=merge_rc,
    )
