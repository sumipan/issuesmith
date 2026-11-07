from __future__ import annotations

import re
import sys
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.rule_ids import rule_id_matches

_SCOPE_MIGRATION = "scope:" + "migration"


def has_acceptance_criteria_section(body: str) -> bool:
    """Return whether the acceptance criteria section exists."""
    heading = get_config().sections["acceptance_criteria"]
    return bool(
        re.search(rf"^##\s+{re.escape(heading)}", body, re.MULTILINE)
    )


def get_unchecked_count(body: str) -> int:
    """Return the number of unchecked checkboxes in the acceptance criteria section."""
    heading = get_config().sections["acceptance_criteria"]
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return 0
    section = match.group(1)
    return len(re.findall(r"^\s*-\s+\[ \]", section, re.MULTILINE))


class AcCompleteRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        ac = get_config().sections["acceptance_criteria"]
        if not has_acceptance_criteria_section(body):
            return [Violation(
                rule_id="ac_complete.ac_section_missing",
                severity="warn",
                message=f"## {ac} section not found",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        unchecked = get_unchecked_count(body)
        if unchecked == 0:
            return []

        return [Violation(
            rule_id="ac_complete.unchecked_ac",
            severity="fail",
            message=f"{unchecked} unchecked item(s) remain in ## {ac}",
            location=None,
            auto_fixable=False,
            fix_hint=None,
        )]


M2Rules = AcCompleteRules

GATE_REGISTRY["ac_complete"] = AcCompleteRules


def _contract_failures_failopen(body: str, repo_root: Path | None = None) -> list[str]:
    try:
        from issuesmith.ac_contract import contract_failures

        return contract_failures(body, repo_root=repo_root)
    except Exception as exc:
        print(f"[m2-gate] contract check skipped (fail-open): {exc}", file=sys.stderr)
        return []


def _proceed_or_contract_retry(
    body: str,
    has_section: bool,
    has_migration_label: bool,
    repo_root: Path | None = None,
) -> dict:
    failures = _contract_failures_failopen(body, repo_root=repo_root)
    if failures:
        return {
            "action": "retry",
            "unchecked_count": 0,
            "has_section": has_section,
            "has_migration_label": has_migration_label,
            "contract_failures": failures,
        }

    if has_migration_label:
        from issuesmith.ac_contract import extract_contract_from_body
        from issuesmith.ops.preflight import check_post_merge

        contract = extract_contract_from_body(body)
        post_merge = contract.get("post_merge") if isinstance(contract, dict) else None
        if isinstance(post_merge, list) and post_merge:
            post_merge_results = check_post_merge(post_merge)
            if any(not ok for ok, _ in post_merge_results):
                return {
                    "action": "migrate",
                    "unchecked_count": 0,
                    "has_section": has_section,
                    "has_migration_label": has_migration_label,
                    "contract_failures": [],
                }

    return {
        "action": "proceed",
        "unchecked_count": 0,
        "has_section": has_section,
        "has_migration_label": has_migration_label,
        "contract_failures": [],
    }


def check_gate(body: str, labels: list[str], repo_root: Path | None = None) -> dict:
    """Return the AC-complete gate verdict (concept rule ids via ``rule_id_matches``)."""
    has_migration_label = _SCOPE_MIGRATION in labels
    violations = AcCompleteRules().check(body, labels)
    rule_ids = {v.rule_id for v in violations}

    if any(rule_id_matches(rid, "ac_complete.ac_section_missing") for rid in rule_ids):
        return _proceed_or_contract_retry(
            body, False, has_migration_label, repo_root=repo_root,
        )

    if not any(rule_id_matches(rid, "ac_complete.unchecked_ac") for rid in rule_ids):
        return _proceed_or_contract_retry(
            body, True, has_migration_label, repo_root=repo_root,
        )

    unchecked = get_unchecked_count(body)

    if has_migration_label:
        return {
            "action": "migrate",
            "unchecked_count": unchecked,
            "has_section": True,
            "has_migration_label": True,
            "contract_failures": [],
        }

    return {
        "action": "retry",
        "unchecked_count": unchecked,
        "has_section": True,
        "has_migration_label": False,
        "contract_failures": [],
    }
