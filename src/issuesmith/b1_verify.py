"""b1_verify.py — B1 成果物の決定論 Verify（#2541 Verify→Recover→Re-verify 契約）。

既存の gate rules（cp1 / b1_ac_format / b1_migration）を 1 コマンドに束ね、
violations を VERIFY_FAILED_CHECKS 形式のレポートとして出力する。
新しい検証ロジックは持たない（ルールの単一情報源は gate_rules/）。

除外: `cp1.intentional_hold`（cp1_must_fail / scope:milestone による意図的保留）は
B1 成果物の不備ではなく CP1 判定用のシグナルなので、Verify 失敗として扱わない。
Violations whose severity is not `fail` (e.g. `warn`) are not counted as Verify failures either (#4076).

Oscillation detection: when the previous report is passed via `--prev-report FILE` (`-` for stdin),
a `b1_verify.oscillation_detected` violation is added if the count for any rule_id grew compared
with the previous run (#4076).
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

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
)
_EXCLUDED_RULE_IDS = frozenset({"cp1.intentional_hold"})


def collect_violations(body: str, labels: list[str]):
    violations = []
    for gate in _GATES:
        violations.extend(GATE_REGISTRY[gate]().check(body, labels))
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


def main() -> int:
    from ghdag.forge import get_forge

    parser = argparse.ArgumentParser(prog="b1_verify")
    parser.add_argument("issue_number", type=int)
    parser.add_argument("--prev-report", default=None, metavar="FILE")
    args = parser.parse_args(sys.argv[1:])

    data = get_forge().issue_get(args.issue_number, fields=["body", "labels"])
    body = data["body"] or ""
    labels = [label["name"] for label in data.get("labels", [])]

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
