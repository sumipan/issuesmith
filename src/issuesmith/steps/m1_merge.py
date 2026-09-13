"""M1 merge Python step — replaces m1-merge.md bash (#3164 / #3060).

Always returns exit_code=0 with pipeline_status=MERGE_REPORTED so ghdag
continues and m1-recover (LLM) can judge / recover.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ghdag.forge import ForgePort, get_forge

from issuesmith.config import StepConfig
from issuesmith.steps.base import StepContext, StepResult

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


def _head_param(repo: str, branch: str) -> str:
    branch = branch.strip()
    if not branch:
        return ""
    if ":" in branch:
        return branch
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    return f"{owner}:{branch}" if owner else branch


def _list_pulls(
    client: ForgePort,
    repo: str,
    *,
    state: str = "open",
    head: str | None = None,
) -> list[dict[str, Any]]:
    if not repo:
        return []
    path = f"repos/{repo}/pulls?state={state}&per_page=100"
    if head:
        path += f"&head={urllib.parse.quote(head, safe='')}"
    try:
        data = client.api_request(path)
    except Exception as exc:
        print(f"PR list failed ({exc})", file=sys.stderr)
        return []
    return data if isinstance(data, list) else []


def _find_pr(
    client: ForgePort,
    repo: str,
    branch: str,
    issue_number: int,
) -> tuple[int | None, str, str]:
    """Progressive PR search: branch → title → body Refs #N → draft → closed.

    Returns (number, state, stage).
    """
    branch = branch.strip()
    refs_marker = f"Refs #{issue_number}"
    title_markers = (f"#{issue_number}", f"Issue #{issue_number}")

    # Stage 1: branch head (open)
    if branch:
        head = _head_param(repo, branch)
        pulls = _list_pulls(client, repo, state="open", head=head)
        if pulls and isinstance(pulls[0], dict) and isinstance(pulls[0].get("number"), int):
            p = pulls[0]
            return p["number"], str(p.get("state") or ""), "branch"

    open_pulls = _list_pulls(client, repo, state="open")

    # Stage 2: title
    for p in open_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        title = p.get("title") or ""
        if branch and branch in title:
            return p["number"], str(p.get("state") or ""), "title"
        if any(m in title for m in title_markers):
            return p["number"], str(p.get("state") or ""), "title"

    # Stage 3: body Refs #N
    for p in open_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        body = p.get("body") or ""
        if refs_marker in body:
            return p["number"], str(p.get("state") or ""), "body_refs"

    # Stage 4: draft PRs (open list already includes drafts; match head/title)
    for p in open_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        if p.get("draft") is not True:
            continue
        head_ref = (p.get("head") or {}).get("ref") or ""
        title = p.get("title") or ""
        if (branch and (head_ref == branch or branch in title)) or any(
            m in title for m in title_markers
        ):
            return p["number"], str(p.get("state") or ""), "draft"

    # Stage 5: include closed
    all_pulls = _list_pulls(client, repo, state="all")
    for p in all_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        head_ref = (p.get("head") or {}).get("ref") or ""
        if branch and head_ref == branch:
            return p["number"], str(p.get("state") or ""), "closed"
    for p in all_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        title = p.get("title") or ""
        body = p.get("body") or ""
        if any(m in title for m in title_markers) or refs_marker in body:
            return p["number"], str(p.get("state") or ""), "closed"

    return None, "", ""


def _is_already_merged(client: ForgePort, repo: str, number: int) -> bool:
    """REST `.merged` (CLAUDE.md §10: true/false rc0; missing → treat as false)."""
    path = f"repos/{repo}/pulls/{number}" if repo else f"pulls/{number}"
    try:
        detail = client.api_request(path)
    except Exception as exc:
        print(f"PR detail failed ({exc})", file=sys.stderr)
        return False
    if not isinstance(detail, dict):
        return False
    return detail.get("merged") is True


def _graphql_merge_state(
    client: ForgePort, repo: str, number: int
) -> dict[str, str]:
    """Fetch mergeStateStatus via GraphQL; fall back to ForgePort.pr_get."""
    owner, _, name = repo.partition("/")
    headers_fn = getattr(client, "_headers", None)
    if owner and name and callable(headers_fn):
        try:
            from ghdag.github_client import GRAPHQL_URL

            query = """
            query($owner:String!,$name:String!,$number:Int!) {
              repository(owner:$owner, name:$name) {
                pullRequest(number:$number) {
                  mergeStateStatus
                  mergeable
                  state
                  headRefName
                }
              }
            }
            """
            payload = json.dumps(
                {
                    "query": query,
                    "variables": {"owner": owner, "name": name, "number": number},
                }
            ).encode()
            req = urllib.request.Request(
                GRAPHQL_URL,
                data=payload,
                method="POST",
                headers={**headers_fn(), "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())
            pr = (
                (result.get("data") or {})
                .get("repository", {})
                .get("pullRequest")
            )
            if isinstance(pr, dict) and pr.get("mergeStateStatus"):
                return {
                    "mergeStateStatus": str(pr.get("mergeStateStatus") or "UNKNOWN"),
                    "mergeable": str(pr.get("mergeable") or "UNKNOWN"),
                    "state": str(pr.get("state") or ""),
                    "headRefName": str(pr.get("headRefName") or ""),
                }
        except Exception as exc:
            print(f"GraphQL mergeStateStatus failed ({exc}); falling back to pr_get", file=sys.stderr)

    detail = client.pr_get(number, repo=repo or None)
    return {
        "mergeStateStatus": str(detail.get("mergeStateStatus") or "UNKNOWN"),
        "mergeable": str(detail.get("mergeable") or "UNKNOWN"),
        "state": str(detail.get("state") or ""),
        "headRefName": str(detail.get("headRefName") or ""),
    }


def _get_merge_state(client: ForgePort, repo: str, number: int) -> dict[str, str]:
    return _graphql_merge_state(client, repo, number)


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
    """Run git merge-tree; return (CLEAN|CONFLICT|SKIPPED, verified)."""
    if not worktree or not Path(worktree).is_dir() or not head_ref:
        return "SKIPPED", False
    fetch = subprocess.run(
        ["git", "fetch", "origin", base_branch, head_ref],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if fetch.returncode != 0:
        print(f"LOCAL_VERIFY: SKIPPED (fetch failed): {fetch.stderr.strip()}", file=sys.stderr)
        return "SKIPPED", False
    tree = subprocess.run(
        [
            "git",
            "merge-tree",
            "--write-tree",
            f"origin/{base_branch}",
            f"origin/{head_ref}",
        ],
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if tree.returncode == 0:
        print("LOCAL_VERIFY: CLEAN (no conflicts detected by merge-tree)")
        return "CLEAN", True
    print(f"LOCAL_VERIFY: CONFLICT (merge-tree exit code={tree.returncode})")
    return "CONFLICT", False


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


def _find_companion_pr(
    client: ForgePort, issue_repo: str, branch: str
) -> int | None:
    companion_branch = f"{branch.strip()}-diary"
    if not issue_repo or not branch.strip():
        return None
    head = _head_param(issue_repo, companion_branch)
    pulls = _list_pulls(client, issue_repo, state="open", head=head)
    if pulls and isinstance(pulls[0], dict) and isinstance(pulls[0].get("number"), int):
        return pulls[0]["number"]
    return None


def _companion_ready(
    client: ForgePort, issue_repo: str, number: int
) -> tuple[bool, str, bool]:
    """Return (ready, review_decision, ci_ok)."""
    decision = ""
    try:
        reviews = client.api_request(f"repos/{issue_repo}/pulls/{number}/reviews")
        if isinstance(reviews, list):
            for rev in reversed(reviews):
                if isinstance(rev, dict) and rev.get("state"):
                    # APPROVED / CHANGES_REQUESTED / COMMENTED
                    state = str(rev.get("state") or "").upper()
                    if state in ("APPROVED", "CHANGES_REQUESTED"):
                        decision = state
                        break
    except Exception as exc:
        print(f"companion reviews failed ({exc})", file=sys.stderr)

    ci_ok = False
    try:
        checks = client.pr_checks(number, repo=issue_repo)
        if checks:
            ci_ok = all(
                str(c.get("conclusion") or "").lower() == "success" for c in checks
            )
        else:
            ci_ok = True  # no checks → treat as pass (align with empty rollup edge)
    except Exception as exc:
        print(f"companion checks failed ({exc})", file=sys.stderr)
        ci_ok = False

    ready = decision == "APPROVED" and ci_ok
    return ready, decision, ci_ok


def _post_merge_pytest(work_dir: str) -> int:
    if not work_dir or not Path(work_dir).is_dir():
        print("POST_MERGE_WORKTREE: (not present, skipping)")
        return 0
    print(f"POST_MERGE_WORKTREE: {work_dir}")
    subprocess.run(
        ["git", "pull", "origin", "HEAD"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    env = os.environ.copy()
    src = str(Path(work_dir) / "src")
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}"
    proc = subprocess.run(
        ["python3", "-m", "pytest", "-q"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    print("\n".join(out.splitlines()[-30:]))
    return int(proc.returncode)


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

    # ---- companion check (has_diary_changes only) ----
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
                        "## M1: companion PR 未承認またはCI未通過\n\n"
                        f"companion PR (#{companion_pr}, branch: {ctx.branch}-diary) "
                        "が承認済みかつ CI パス済みになってから再実行してください。\n\n"
                        "PIPELINE_STATUS: MERGE_PENDING",
                    )
                except Exception as exc:
                    print(f"companion comment failed: {exc}", file=sys.stderr)
                return _reported(["companion_pr_not_ready"])
            print("COMPANION_CHECK: OK")
    else:
        print("(has_diary_changes != true — skipped)")
    print("")

    # ---- PR search ----
    print("## [pr_search]")
    pr_number, pr_state, stage = _find_pr(client, pr_repo, ctx.branch, issue_number)
    if pr_number is None:
        print(f"PR_NOT_FOUND (branch: {ctx.branch})")
        return _reported(["pr_search"])
    print(f"PR_NUMBER: {pr_number}")
    print(f"PR_STATE: {pr_state}")
    print(f"PR_SEARCH_STAGE: {stage}")
    print("")

    # ---- already merged ----
    print("## [already_merged_check]")
    if _is_already_merged(client, pr_repo, pr_number):
        print("PR_MERGED: true")
        print("ALREADY_MERGED: yes")
        print("")
        return _reported([], PR_NUMBER=pr_number)
    print("PR_MERGED: false")
    print("ALREADY_MERGED: no")
    print("")

    # ---- mergeStateStatus (BLOCKED retry) ----
    print("## [merge_state]")
    merge_info = _poll_merge_state(client, pr_repo, pr_number)
    merge_state = merge_info.get("mergeStateStatus") or "UNKNOWN"
    head_ref = merge_info.get("headRefName") or ctx.branch
    print(f"MERGE_STATE: {merge_state}")
    print(f"MERGEABLE: {merge_info.get('mergeable')}")
    print(f"PR_HEAD_REF: {head_ref}")
    print("")

    # ---- merge-tree when UNKNOWN ----
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

    # ---- M2 gate preflight ----
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
                "## M1 中断: M2 ゲート未通過\n\n"
                "PR マージ前の M2 ゲート検査で未解決の問題が検出されました:\n\n- "
                + "\n- ".join(fail_msgs)
                + "\n\n受け入れ条件をすべてチェックしてから再実行してください。\n\n"
                "PIPELINE_STATUS: MERGE_PENDING",
            )
        except Exception as exc:
            print(f"gate comment failed: {exc}", file=sys.stderr)
        print("GATE_PREFLIGHT_M2: BLOCKED")
        return _reported(["gate_preflight_m2"])
    print("GATE_PREFLIGHT_M2: OK")
    print("")

    # ---- merge attempt ----
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

    # ---- companion merge ----
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

    # ---- post-merge pytest ----
    print("## [post_merge_test]")
    posttest_rc = 0
    if merge_attempted and merge_rc == 0:
        posttest_rc = _post_merge_pytest(_worktree_path(ctx))
        if posttest_rc != 0:
            failed.append("post_merge_test")
            # PR はマージ済みだが post_merge_test 失敗 → M1r/M2 が
            # merge-running → merge-done を辿れるようラベルを先に付与 (#3221 AC-6)
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
