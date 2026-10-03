"""
cp1_gate.py — CP1 pattern check gate (thin wrapper)

The check logic is delegated to gate_rules.cp1.Cp1Rules.
"""

from __future__ import annotations

import json
import sys

from ghdag.forge import get_forge

from issuesmith.gate_rules.b1_migration import B1MigrationRules
from issuesmith.gate_rules.cp1 import Cp1Rules
from issuesmith.gate_rules.milestone_consistency import MilestoneConsistencyRules
from issuesmith.gate_rules.scope_breadth import ScopeBreadthRules
from issuesmith.gate_rules.scope_coupling import ScopeCouplingRules


def check_gate(body: str, labels: list[str] | None = None) -> dict:
    """Return the CP1 gate verdict.

    For scope:migration Issues, CP1 also enforces the migration deterministic rules
    (steps, runtime state survey, migration verification test contract). The B1
    preflight is advisory (B1 does not fail on defects), so this is the enforcement
    point of the mechanical block.

    A contradiction between a split-plan pattern and scope:milestone is always checked
    regardless of labels (the missing label is itself the violation, so a conditional
    skip is not possible).

    Even when allow_paths exceeds the breadth gate, ScopeBreadthRules.check emits no
    violation and continues if it can narrow them deterministically from the changed
    files table + paths_must_exist (#3487). When narrowing happens, autofix_note /
    autofix_new_allow_paths hold the rewrite (main() persists the Issue body via forge).

    ScopeCouplingRules detects files missing from allow_paths and adds them with
    auto-widen (#3520). The coupling autofix wins over the breadth autofix.

    Returns:
        {"status": "PASS"|"FAIL", "reasons": list[str], "intentional_hold": bool,
         "autofix_note": str | None, "autofix_new_allow_paths": list[str] | None}
    """
    label_list = labels or []
    scope_rule = ScopeBreadthRules()
    coupling_rule = ScopeCouplingRules()
    violations = Cp1Rules().check(body, label_list)
    violations = violations + MilestoneConsistencyRules().check(body, label_list)
    violations = violations + scope_rule.check(body, label_list)
    violations = violations + coupling_rule.check(body, label_list)
    if "scope:migration" in label_list:
        violations = violations + B1MigrationRules().check(body, label_list)
    reasons = [v.message for v in violations]
    intentional_hold = any(v.rule_id == "cp1.intentional_hold" for v in violations)
    autofix_note = coupling_rule.autofix_note or scope_rule.autofix_note
    autofix_new_allow_paths = (
        coupling_rule.autofix_new_allow_paths or scope_rule.autofix_new_allow_paths
    )
    return {
        "status": "FAIL" if violations else "PASS",
        "reasons": reasons,
        "intentional_hold": intentional_hold,
        "autofix_note": autofix_note,
        "autofix_new_allow_paths": autofix_new_allow_paths,
    }


def main() -> None:
    """CLI: python -m issuesmith cp1-gate <issue_number>"""
    issue_number = int(sys.argv[1])

    forge = get_forge()
    data = forge.issue_get(issue_number, fields=["body", "labels"])
    body = data["body"]
    labels = [label["name"] for label in data.get("labels", [])]

    gate_result = check_gate(body, labels)

    new_allow_paths = gate_result.get("autofix_new_allow_paths")
    if new_allow_paths:
        from issuesmith.body_editor import replace_allow_paths

        new_body = replace_allow_paths(body, new_allow_paths)
        if new_body is not None:
            try:
                forge.issue_update(issue_number, body=new_body)
            except Exception as exc:  # noqa: BLE001 — report, don't fail the gate
                print(f"CP1 autofix body update failed: {exc}", file=sys.stderr)
            note = gate_result.get("autofix_note")
        else:
            # Body couldn't be rewritten — don't tell the Issue a narrowing
            # happened that wasn't actually persisted.
            note = None
        if note:
            try:
                forge.issue_comment(issue_number, note)
            except Exception as exc:  # noqa: BLE001
                print(f"CP1 autofix comment failed: {exc}", file=sys.stderr)

    print(json.dumps(gate_result))
