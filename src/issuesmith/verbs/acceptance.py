"""Acceptance verbs — gate checks, contract, and review tier (#6582 / nexus #6477)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ghdag.forge import ForgePort

from issuesmith.ac_complete_entry import check_gate, check_gate_multi_root
from issuesmith.ac_contract import (
    GateMaterializationError,
    cleanup_gate_root,
    extract_contract_from_body,
    materialize_gate_root,
    run_checks,
)
from issuesmith.gate_rules.ac_complete_rules import get_unchecked_count
from issuesmith.pr_scope import DIFF_LINES_FALLBACK, find_pr_for_branch
from issuesmith.step_tier import pr_diff_tier

__all__ = [
    "AcceptanceError",
    "AcceptanceVerdict",
    "ReviewFacts",
    "check_acceptance",
    "check_contract",
    "extract_contract",
    "review_tier",
    "unchecked_acceptance_count",
]

Action = Literal["proceed", "retry", "migrate"]
Tier = Literal["light", "heavy"]


class AcceptanceError(RuntimeError):
    """Temporary gate root (origin/base) could not be materialized."""


@dataclass(frozen=True, slots=True)
class AcceptanceVerdict:
    action: Action
    unchecked_count: int
    contract_failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    pr_number: int | None
    head_sha: str | None
    diff_lines: int
    unchecked: int
    tier: Tier


def unchecked_acceptance_count(body: str) -> int:
    """Count unchecked ``- [ ]`` items inside the configured acceptance section only."""
    return get_unchecked_count(body)


def extract_contract(body: str) -> dict | None:
    """Parse the YAML contract block from the acceptance criteria section."""
    return extract_contract_from_body(body)


def check_contract(contract: dict, repo_root: Path) -> list[dict]:
    """Run contract checks and return ``run_checks`` records."""
    return run_checks(contract, repo_root)


def _diff_lines_from_detail(detail: dict | None) -> int:
    if detail is None:
        return DIFF_LINES_FALLBACK
    additions = detail.get("additions")
    deletions = detail.get("deletions")
    if additions is None and deletions is None:
        return DIFF_LINES_FALLBACK
    return int(additions or 0) + int(deletions or 0)


def _head_sha_from_detail(detail: dict | None) -> str | None:
    if detail is None:
        return None
    head = detail.get("head")
    if isinstance(head, dict):
        sha = head.get("sha")
        return sha if isinstance(sha, str) and sha else None
    return None


def review_tier(
    client: ForgePort,
    repo: str,
    branch: str,
    *,
    issue_number: int | None,
    issue_body: str,
    gates_pass: bool,
    light_max_diff_lines: int = 200,
) -> ReviewFacts:
    """Resolve PR diff metrics and light/heavy tier for review steps."""
    pr_number, detail = find_pr_for_branch(
        client,
        repo,
        branch,
        issue_number=issue_number,
        issue_body=issue_body,
    )
    diff_lines = _diff_lines_from_detail(detail)
    unchecked = unchecked_acceptance_count(issue_body)
    tier = pr_diff_tier(
        diff_lines,
        unchecked,
        gates_pass,
        light_max_diff_lines=light_max_diff_lines,
    )
    head_sha = _head_sha_from_detail(detail) if pr_number is not None else None
    return ReviewFacts(
        pr_number=pr_number,
        head_sha=head_sha,
        diff_lines=diff_lines,
        unchecked=unchecked,
        tier=tier,
    )


def _verdict_from_gate(raw: dict) -> AcceptanceVerdict:
    failures = raw.get("contract_failures") or []
    return AcceptanceVerdict(
        action=raw["action"],
        unchecked_count=int(raw["unchecked_count"]),
        contract_failures=tuple(failures),
    )


def check_acceptance(
    body: str,
    labels: list[str],
    *,
    repos: dict[str, Path],
    base_branch: str,
) -> AcceptanceVerdict:
    """Run AC-complete gate checks against one or two materialized ``origin/<base>`` roots."""
    if len(repos) not in (1, 2):
        msg = f"repos must contain 1 or 2 entries, got {len(repos)}"
        raise ValueError(msg)

    materialized: list[tuple[Path, Path]] = []
    repo_roots: dict[str, Path] = {}
    try:
        for slug, repo_cwd in repos.items():
            prefix = f"gate-{slug.rsplit('/', 1)[-1]}-"
            try:
                gate_root = materialize_gate_root(repo_cwd, base_branch, prefix)
            except GateMaterializationError as exc:
                raise AcceptanceError(str(exc)) from exc
            materialized.append((repo_cwd, gate_root))
            repo_roots[slug] = gate_root

        if len(repo_roots) == 1:
            only_root = next(iter(repo_roots.values()))
            raw = check_gate(body, labels, repo_root=only_root)
        else:
            raw = check_gate_multi_root(body, labels, repo_roots)

        return _verdict_from_gate(raw)
    finally:
        for repo_cwd, gate_root in materialized:
            cleanup_gate_root(repo_cwd, gate_root)
