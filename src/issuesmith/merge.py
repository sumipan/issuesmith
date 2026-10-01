"""Reusable merge helpers for issuesmith consumers (#4275).

Public API for PR discovery, merge-state polling, local merge verification,
post-merge pytest, and companion PR checks. Does not reference workflow phase
names, labels, comments, templates, Slack, diary, or ``jobs/`` paths.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ghdag.forge import ForgePort

from issuesmith.forge_api import api_request

_DEFAULT_BLOCKED_BACKOFF = (5.0, 10.0, 20.0)


@dataclass(frozen=True)
class PrSearchResult:
    number: int | None
    state: str
    stage: str


@dataclass(frozen=True)
class MergeStateInfo:
    merge_state_status: str
    mergeable: str
    state: str
    head_ref_name: str

    @classmethod
    def from_mapping(cls, data: dict[str, str]) -> MergeStateInfo:
        return cls(
            merge_state_status=str(data.get("mergeStateStatus") or "UNKNOWN"),
            mergeable=str(data.get("mergeable") or "UNKNOWN"),
            state=str(data.get("state") or ""),
            head_ref_name=str(data.get("headRefName") or ""),
        )


class MergeStateTimeoutError(Exception):
    """Raised when merge state does not reach a non-BLOCKED status before timeout."""

    def __init__(self, pr_number: int, last_state: MergeStateInfo) -> None:
        self.pr_number = pr_number
        self.last_state = last_state
        super().__init__(
            f"merge state timeout for PR #{pr_number}: "
            f"last mergeStateStatus={last_state.merge_state_status}"
        )


@dataclass(frozen=True)
class LocalMergeVerifyResult:
    result: str
    verified: bool
    fetch_command: list[str] | None
    merge_tree_command: list[str] | None
    cwd: str | None


@dataclass(frozen=True)
class PostMergeTestResult:
    exit_code: int
    command: list[str]
    cwd: str
    pull_command: list[str] | None


@dataclass(frozen=True)
class CompanionReadyResult:
    ready: bool
    review_decision: str
    ci_ok: bool


def _head_param(repo: str, branch: str) -> str:
    branch = branch.strip()
    if not branch:
        return ""
    if ":" in branch:
        return branch
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    return f"{owner}:{branch}" if owner else branch


def list_pulls(
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
        data = api_request(client, path)
    except Exception as exc:
        print(f"PR list failed ({exc})", file=sys.stderr)
        return []
    return data if isinstance(data, list) else []


def find_pr(
    client: ForgePort,
    repo: str,
    branch: str,
    issue_number: int,
) -> PrSearchResult:
    """Progressive PR search: branch -> title -> body Refs #N -> draft -> closed."""
    branch = branch.strip()
    refs_marker = f"Refs #{issue_number}"
    title_markers = (f"#{issue_number}", f"Issue #{issue_number}")

    if branch:
        head = _head_param(repo, branch)
        pulls = list_pulls(client, repo, state="open", head=head)
        if pulls and isinstance(pulls[0], dict) and isinstance(pulls[0].get("number"), int):
            p = pulls[0]
            return PrSearchResult(
                p["number"], str(p.get("state") or ""), "branch"
            )

    open_pulls = list_pulls(client, repo, state="open")

    for p in open_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        title = p.get("title") or ""
        if branch and branch in title:
            return PrSearchResult(p["number"], str(p.get("state") or ""), "title")
        if any(m in title for m in title_markers):
            return PrSearchResult(p["number"], str(p.get("state") or ""), "title")

    for p in open_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        body = p.get("body") or ""
        if refs_marker in body:
            return PrSearchResult(p["number"], str(p.get("state") or ""), "body_refs")

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
            return PrSearchResult(p["number"], str(p.get("state") or ""), "draft")

    all_pulls = list_pulls(client, repo, state="all")
    for p in all_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        head_ref = (p.get("head") or {}).get("ref") or ""
        if branch and head_ref == branch:
            return PrSearchResult(p["number"], str(p.get("state") or ""), "closed")
    for p in all_pulls:
        if not isinstance(p, dict) or not isinstance(p.get("number"), int):
            continue
        title = p.get("title") or ""
        body = p.get("body") or ""
        if any(m in title for m in title_markers) or refs_marker in body:
            return PrSearchResult(p["number"], str(p.get("state") or ""), "closed")

    return PrSearchResult(None, "", "")


def is_already_merged(client: ForgePort, repo: str, number: int) -> bool:
    """Return whether PR *number* is merged (REST ``.merged`` field)."""
    path = f"repos/{repo}/pulls/{number}" if repo else f"pulls/{number}"
    try:
        detail = api_request(client, path)
    except Exception as exc:
        print(f"PR detail failed ({exc})", file=sys.stderr)
        return False
    if not isinstance(detail, dict):
        return False
    return detail.get("merged") is True


def graphql_merge_state(client: ForgePort, repo: str, number: int) -> MergeStateInfo:
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
                return MergeStateInfo.from_mapping(pr)
        except Exception as exc:
            print(
                f"GraphQL mergeStateStatus failed ({exc}); falling back to pr_get",
                file=sys.stderr,
            )

    detail = client.pr_get(number, repo=repo or None)
    return MergeStateInfo.from_mapping(detail)


def get_merge_state(client: ForgePort, repo: str, number: int) -> MergeStateInfo:
    return graphql_merge_state(client, repo, number)


def wait_merge_state(
    client: ForgePort,
    repo: str,
    number: int,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
    blocked_backoff_seconds: tuple[float, ...] = _DEFAULT_BLOCKED_BACKOFF,
    sleep: Callable[[float], None] | None = None,
) -> MergeStateInfo:
    """Poll merge state until non-BLOCKED or *timeout_seconds* elapses.

    Raises :class:`MergeStateTimeoutError` with the last state and PR number.
    """
    sleeper = sleep or time.sleep
    deadline = time.monotonic() + timeout_seconds
    blocked_attempt = 0
    last = MergeStateInfo("UNKNOWN", "UNKNOWN", "", "")

    while time.monotonic() < deadline:
        last = get_merge_state(client, repo, number)
        if last.merge_state_status != "BLOCKED":
            return last
        if blocked_attempt < len(blocked_backoff_seconds):
            sleeper(blocked_backoff_seconds[blocked_attempt])
            blocked_attempt += 1
        else:
            sleeper(poll_interval_seconds)

    raise MergeStateTimeoutError(number, last)


def verify_local_merge(
    worktree: str, base_branch: str, head_ref: str
) -> LocalMergeVerifyResult:
    """Run ``git merge-tree``; return CLEAN / CONFLICT / SKIPPED."""
    if not worktree or not Path(worktree).is_dir() or not head_ref:
        return LocalMergeVerifyResult("SKIPPED", False, None, None, worktree or None)

    fetch_cmd = ["git", "fetch", "origin", base_branch, head_ref]
    fetch = subprocess.run(
        fetch_cmd,
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if fetch.returncode != 0:
        print(f"LOCAL_VERIFY: SKIPPED (fetch failed): {fetch.stderr.strip()}", file=sys.stderr)
        return LocalMergeVerifyResult("SKIPPED", False, fetch_cmd, None, worktree)

    merge_tree_cmd = [
        "git",
        "merge-tree",
        "--write-tree",
        f"origin/{base_branch}",
        f"origin/{head_ref}",
    ]
    tree = subprocess.run(
        merge_tree_cmd,
        cwd=worktree,
        capture_output=True,
        text=True,
        check=False,
    )
    if tree.returncode == 0:
        return LocalMergeVerifyResult("CLEAN", True, fetch_cmd, merge_tree_cmd, worktree)
    return LocalMergeVerifyResult("CONFLICT", False, fetch_cmd, merge_tree_cmd, worktree)


def run_post_merge_pytest(work_dir: str) -> PostMergeTestResult:
    """Run ``python3 -m pytest -q`` in *work_dir* after ``git pull origin HEAD``."""
    pull_cmd = ["git", "pull", "origin", "HEAD"]
    pytest_cmd = ["python3", "-m", "pytest", "-q"]
    if not work_dir or not Path(work_dir).is_dir():
        return PostMergeTestResult(0, pytest_cmd, work_dir or "", None)

    subprocess.run(
        pull_cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    env = os.environ.copy()
    src = str(Path(work_dir) / "src")
    env["PYTHONPATH"] = f"{src}:{env.get('PYTHONPATH', '')}"
    proc = subprocess.run(
        pytest_cmd,
        cwd=work_dir,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return PostMergeTestResult(int(proc.returncode), pytest_cmd, work_dir, pull_cmd)


def find_companion_pr(
    client: ForgePort,
    repo: str,
    branch: str,
    *,
    companion_suffix: str = "-diary",
) -> int | None:
    """Return an open companion PR number for *branch* + *companion_suffix*, if any."""
    companion_branch = f"{branch.strip()}{companion_suffix}"
    if not repo or not branch.strip():
        return None
    head = _head_param(repo, companion_branch)
    pulls = list_pulls(client, repo, state="open", head=head)
    if pulls and isinstance(pulls[0], dict) and isinstance(pulls[0].get("number"), int):
        return pulls[0]["number"]
    return None


def check_companion_ready(
    client: ForgePort, repo: str, number: int
) -> CompanionReadyResult:
    """Return review decision and CI rollup for companion PR *number*."""
    decision = ""
    try:
        reviews = api_request(client, f"repos/{repo}/pulls/{number}/reviews")
        if isinstance(reviews, list):
            for rev in reversed(reviews):
                if isinstance(rev, dict) and rev.get("state"):
                    state = str(rev.get("state") or "").upper()
                    if state in ("APPROVED", "CHANGES_REQUESTED"):
                        decision = state
                        break
    except Exception as exc:
        print(f"companion reviews failed ({exc})", file=sys.stderr)

    ci_ok = False
    try:
        checks = client.pr_checks(number, repo=repo)
        if checks:
            ci_ok = all(
                str(c.get("conclusion") or "").lower() == "success" for c in checks
            )
        else:
            ci_ok = True
    except Exception as exc:
        print(f"companion checks failed ({exc})", file=sys.stderr)
        ci_ok = False

    ready = decision == "APPROVED" and ci_ok
    return CompanionReadyResult(ready, decision, ci_ok)


__all__ = [
    "CompanionReadyResult",
    "LocalMergeVerifyResult",
    "MergeStateInfo",
    "MergeStateTimeoutError",
    "PostMergeTestResult",
    "PrSearchResult",
    "check_companion_ready",
    "find_companion_pr",
    "find_pr",
    "get_merge_state",
    "graphql_merge_state",
    "is_already_merged",
    "list_pulls",
    "run_post_merge_pytest",
    "verify_local_merge",
    "wait_merge_state",
]
