"""find_pr / merge_state / merge_pr verbs — public extraction from steps.m1_merge."""

from __future__ import annotations

from ghdag.forge import ForgePort

from issuesmith.steps.m1_merge import _find_pr as _m1_find_pr
from issuesmith.steps.m1_merge import _get_merge_state


def find_pr(client: ForgePort, repo: str, branch: str, issue: int) -> int | None:
    """Return the PR number for *branch*/*issue* in *repo*, or None if not found."""
    number, _state, _stage = _m1_find_pr(client, repo, branch, issue)
    return number


def merge_state(client: ForgePort, repo: str, pr: int) -> dict:
    """Return merge-state info dict for PR *pr* in *repo*."""
    return _get_merge_state(client, repo, pr)


def merge_pr(client: ForgePort, repo: str, pr: int, *, delete_branch: bool = True) -> None:
    """Merge PR *pr* in *repo* using a merge commit."""
    client.pr_merge(pr, method="merge", delete_branch=delete_branch, repo=repo or None)


__all__ = ["find_pr", "merge_state", "merge_pr"]
