"""Acceptance contract gate — paths and greps_must_be_empty on a worktree."""

from __future__ import annotations

from pathlib import Path

from issuesmith.ac_contract import (
    GrepError,
    extract_contract_from_body,
    grep_matches,
    is_invalid_contract_path,
    normalize_grep_entry,
    run_checks,
)
from issuesmith.gate_registry import Violation

_FIX_HINT = (
    "Do not delete, rename, or merge paths covered by the acceptance contract "
    "(restore paths listed in paths_must_exist; remove paths listed in "
    "paths_must_not_exist)."
)

_GREP_FIX_HINT = (
    "Remove grep matches in the worktree. If a match cannot be removed, add the "
    "path to greps_must_be_empty exclude in the Issue body (B1 phase)."
)


def _coerce_filtered_paths(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for entry in value:
        if isinstance(entry, str) and not is_invalid_contract_path(entry):
            out.append(entry)
    return out


def _record_message(record: dict) -> str:
    check = record["check"]
    path = record["path"]
    detail = record.get("detail") or ""
    base = f"Acceptance contract {check}: {path}"
    if detail:
        base = f"{base}. {detail}"
    git_log = record.get("git_log") or ""
    if git_log:
        base = f"{base}. Git log: {git_log}"
    return base


def _violations_from_records(records: list[dict]) -> list[Violation]:
    violations: list[Violation] = []
    for record in records:
        if record.get("result") != "FAIL":
            continue
        check = record["check"]
        violations.append(
            Violation(
                rule_id=f"ac_contract.{check}",
                severity="fail",
                message=_record_message(record),
                location=record["path"],
                auto_fixable=False,
                fix_hint=_FIX_HINT,
            )
        )
    return violations


class AcContractGate:
    """RequiresGate adapter: evaluate path acceptance contract against a worktree."""

    def __init__(self, worktree_path: Path) -> None:
        self._worktree_path = worktree_path

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        contract = extract_contract_from_body(body)
        if contract is None:
            return []

        sub_contract = {
            "paths_must_exist": _coerce_filtered_paths(
                contract.get("paths_must_exist")
            ),
            "paths_must_not_exist": _coerce_filtered_paths(
                contract.get("paths_must_not_exist")
            ),
        }
        records = run_checks(sub_contract, self._worktree_path)
        violations = _violations_from_records(records)

        grep_entries = contract.get("greps_must_be_empty")
        if not isinstance(grep_entries, list):
            return violations
        for raw in grep_entries:
            normalized, err = normalize_grep_entry(raw)
            if err is not None:
                continue
            assert normalized is not None
            try:
                hits = grep_matches(self._worktree_path, normalized)
            except GrepError:
                continue
            for rel_path in hits:
                violations.append(
                    Violation(
                        rule_id="ac_contract.greps_must_be_empty",
                        severity="fail",
                        message=(
                            f"Acceptance contract greps_must_be_empty: "
                            f"{normalized['pattern']} matched {rel_path}"
                        ),
                        location=rel_path,
                        auto_fixable=False,
                        fix_hint=_GREP_FIX_HINT,
                    )
                )
        return violations


__all__ = ["AcContractGate"]
