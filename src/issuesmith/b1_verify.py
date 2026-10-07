"""b1_verify.py — deterministic Verify of B1 output (#2541 Verify->Recover->Re-verify contract).

Bundles the existing gate rules (cp1 / b1_ac_format / b1_migration) into one command
and prints the violations as a VERIFY_FAILED_CHECKS report.
It has no verification logic of its own (gate_rules/ is the single source of rules).

Excluded: `cp1.intentional_hold` (intentional hold via cp1_must_fail / scope:milestone)
is a signal for the CP1 verdict, not a defect in the B1 output, so it is not a Verify failure.
Violations whose severity is not `fail` (e.g. `warn`) are not counted as Verify failures either (#4076).

Oscillation detection: when the previous report is passed via `--prev-report FILE` (`-` for stdin),
a `b1_verify.oscillation_detected` violation is added if the count for any rule_id grew compared
with the previous run (#4076).

Deterministic recovery (#4191): :func:`apply_deterministic_recovery` promotes
oversized Issues and applies auto-fixable milestone helpers before any LLM recovery.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass

from ghdag.forge import ForgePort
from ghdag.workflow.gates import Violation

from issuesmith.gate_rules import GATE_REGISTRY

_GATES = (
    "cp1",
    "b1_ac_format",
    "b1_migration",
    "milestone_consistency",
    "scope_breadth",
    "scope_coupling",
    "scope_size",
    "b1_milestone_subdesign",
    "pin_bump",
)
_EXCLUDED_RULE_IDS = frozenset({"cp1.intentional_hold"})
_MILESTONE_LABEL = "scope:milestone"
_REAL_LABEL_GATES = frozenset({"milestone_consistency"})
_SCOPE_SIZE_PROMOTE_IDS = frozenset(
    {"scope_size.too_many_files", "scope_size.too_many_concerns"}
)


def assumed_labels(body: str, labels: list[str]) -> list[str]:
    """Return labels plus scope:milestone when the body has a split-plan pattern but no label."""
    if _MILESTONE_LABEL in labels:
        return list(labels)
    from issuesmith.gate_rules.milestone_consistency import MilestoneConsistencyRules

    mc = MilestoneConsistencyRules().check(body, labels)
    if any(v.rule_id == "milestone_consistency.label_missing" for v in mc):
        return [*labels, _MILESTONE_LABEL]
    return list(labels)


def check_paths_must_not_exist_validity(body: str, labels: list[str] | None = None):
    """B1 helper: reject ``paths_must_not_exist`` this Issue cannot satisfy (#4257).

    Gate logic lives in ``gate_rules.scope_coupling``; this wrapper resolves the
    target clone and delegates so callers outside the gate registry can reuse it.
    """
    from issuesmith.config import get_config
    from issuesmith.context_hook import parse_issue_metadata
    from issuesmith.gate_rules.scope_coupling import check_paths_must_not_exist_contract
    from issuesmith.scope_gate import resolve_scope_root

    try:
        metadata = parse_issue_metadata(body)
    except Exception:
        return []
    root = resolve_scope_root(metadata, get_config())
    if root is None:
        return []
    return check_paths_must_not_exist_contract(body, root)


def collect_violations(body: str, labels: list[str]):
    _assumed = assumed_labels(body, labels)
    violations = []
    for gate in _GATES:
        gate_labels = labels if gate in _REAL_LABEL_GATES else _assumed
        violations.extend(GATE_REGISTRY[gate]().check(body, gate_labels))
    return [
        v for v in violations
        if v.rule_id not in _EXCLUDED_RULE_IDS and getattr(v, "severity", "fail") == "fail"
    ]


def _parse_prev_counts(report: str) -> Counter:
    """rule_id counts from a previous report's ``VERIFY_FAILED_CHECKS:`` line."""
    for line in report.splitlines():
        if line.startswith("VERIFY_FAILED_CHECKS:"):
            ids = line[len("VERIFY_FAILED_CHECKS:"):].split()
            return Counter(i for i in ids if i != "(none)")
    return Counter()


def detect_oscillation(violations, prev_report: str) -> Violation | None:
    """Fail when any rule_id has more violations than in ``prev_report`` (recovery made it worse)."""
    prev_counts = _parse_prev_counts(prev_report)
    curr_counts = Counter(v.rule_id for v in violations)
    oscillating = [rid for rid, cnt in curr_counts.items() if cnt > prev_counts.get(rid, 0)]
    if not oscillating:
        return None
    return Violation(
        rule_id="b1_verify.oscillation_detected",
        severity="fail",
        message=(
            "Violation count increased after recovery (oscillation). "
            f"rule_id: {', '.join(oscillating)}"
        ),
        location=None,
        auto_fixable=False,
        fix_hint="Stop the recovery loop and fix the root cause (the gate logic) directly.",
    )


def format_report(violations) -> str:
    if not violations:
        return "VERIFY_FAILED_CHECKS: (none)\n"
    lines = ["VERIFY_FAILED_CHECKS: " + " ".join(v.rule_id for v in violations), ""]
    for v in violations:
        lines.append(f"## {v.rule_id}")
        lines.append(v.message)
        if v.fix_hint:
            lines.append("FIX_HINT:")
            lines.append(v.fix_hint)
        lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class DeterministicRecoveryResult:
    """Result of :func:`apply_deterministic_recovery`."""

    body: str
    labels: list[str]
    applied: tuple[str, ...]
    remaining: tuple[Violation, ...]
    llm_violations: tuple[Violation, ...]
    can_done: bool
    unresolved_reason: str | None


def apply_deterministic_recovery(
    client: ForgePort,
    issue: int,
    body: str,
    labels: list[str],
    *,
    dry_run: bool = False,
    persist: bool = True,
) -> DeterministicRecoveryResult:
    """Apply auto-fixable helpers and scope_size→milestone promotion before LLM recovery.

    Order:
    1. If ``scope_size.too_many_files`` / ``too_many_concerns`` fire, rewrite the
       body via :func:`promote_oversized_issue_body` and ensure label + milestone
       object via :func:`fix_label_missing`.
    2. Apply every ``auto_fixable=True`` milestone_consistency helper and add
       inferred sibling dependencies to the sub plan (cycles excluded, #4825).
    3. Persist the body when it changed (unless ``dry_run`` / ``persist=False``).
    4. Re-run Verify. ``can_done`` is True only when no fail violations remain.
       Remaining non-auto-fixable violations are returned as ``llm_violations``.
    """
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        apply_inferred_dependency_fixes,
    )
    from issuesmith.gate_rules.milestone_consistency import (
        apply_auto_fixable_helpers,
        apply_body_autofixes,
        fix_label_missing,
    )
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    original_body = body
    new_labels = list(labels)
    applied: list[str] = []

    before = collect_violations(body, new_labels)
    before_ids = {v.rule_id for v in before}

    if before_ids & _SCOPE_SIZE_PROMOTE_IDS:
        promoted = promote_oversized_issue_body(body)
        if promoted != body:
            body = promoted
            applied.append("scope_size.promote_to_milestone")
        # Promotion always requires label + milestone object together.
        fix_label_missing(client, issue, dry_run=dry_run)
        if _MILESTONE_LABEL not in new_labels:
            new_labels.append(_MILESTONE_LABEL)
        applied.append("milestone_consistency.fix_label_missing")

    # Body autofixes (english headers / misplaced plan) — safe and idempotent.
    body, body_applied = apply_body_autofixes(body)
    # Sibling new-file references missing from the depends-on column (#4825);
    # edges closing a cycle are left for dependency_cycle to report.
    body, dep_applied = apply_inferred_dependency_fixes(body)
    for fix_id in (*body_applied, *dep_applied):
        if fix_id not in applied:
            applied.append(fix_id)

    # Remaining auto-fixable helpers (e.g. label_missing after a hand-written plan).
    mid = collect_violations(body, new_labels)
    body, new_labels, helper_applied = apply_auto_fixable_helpers(
        client, issue, body, new_labels, mid, dry_run=dry_run
    )
    for fix_id in helper_applied:
        if fix_id not in applied:
            applied.append(fix_id)

    if persist and not dry_run and body != original_body:
        client.issue_update(issue, body=body)

    remaining = collect_violations(body, new_labels)
    remaining_ids = {v.rule_id for v in remaining}
    can_done = not remaining
    unresolved_reason: str | None = None
    if not can_done:
        still = sorted(before_ids & remaining_ids)
        if still:
            unresolved_reason = (
                "same rule_id(s) remain after deterministic recovery: "
                + ", ".join(still)
            )
        else:
            unresolved_reason = (
                "verify still failing after deterministic recovery: "
                + ", ".join(sorted(remaining_ids))
            )

    llm_violations = tuple(v for v in remaining if not v.auto_fixable)
    return DeterministicRecoveryResult(
        body=body,
        labels=new_labels,
        applied=tuple(applied),
        remaining=tuple(remaining),
        llm_violations=llm_violations,
        can_done=can_done,
        unresolved_reason=unresolved_reason,
    )


def main() -> int:
    from ghdag.forge import get_forge

    parser = argparse.ArgumentParser(prog="b1_verify")
    parser.add_argument("issue_number", type=int)
    parser.add_argument("--prev-report", default=None, metavar="FILE")
    parser.add_argument(
        "--apply-deterministic",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Apply deterministic recovery helpers before reporting "
            "(default: enabled; nexus #4191)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not persist deterministic body/label/milestone changes",
    )
    args = parser.parse_args(sys.argv[1:])

    forge = get_forge()
    data = forge.issue_get(args.issue_number, fields=["body", "labels", "milestone"])
    body = data["body"] or ""
    labels = [label["name"] for label in data.get("labels", [])]

    if args.apply_deterministic:
        result = apply_deterministic_recovery(
            forge,
            args.issue_number,
            body,
            labels,
            dry_run=args.dry_run,
            persist=not args.dry_run,
        )
        body = result.body
        labels = result.labels
        if result.applied:
            sys.stdout.write(
                "DETERMINISTIC_APPLIED: " + " ".join(result.applied) + "\n"
            )
        if result.can_done:
            sys.stdout.write("B1_RECOVERY: DONE\n")
        elif result.unresolved_reason:
            sys.stdout.write("B1_RECOVERY: UNRESOLVED\n")
            sys.stdout.write(f"REASON: {result.unresolved_reason}\n")
        violations = list(result.remaining)
    else:
        violations = collect_violations(body, labels)

    if args.prev_report is not None:
        if args.prev_report == "-":
            prev_report = sys.stdin.read()
        else:
            with open(args.prev_report, encoding="utf-8") as fh:
                prev_report = fh.read()
        oscillation = detect_oscillation(violations, prev_report)
        if oscillation is not None:
            violations.append(oscillation)
    sys.stdout.write(format_report(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
