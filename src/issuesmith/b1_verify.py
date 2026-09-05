"""b1_verify.py — B1 成果物の決定論 Verify（#2541 Verify→Recover→Re-verify 契約）。

既存の gate rules（cp1 / b1_ac_format / b1_migration）を 1 コマンドに束ね、
violations を VERIFY_FAILED_CHECKS 形式のレポートとして出力する。
新しい検証ロジックは持たない（ルールの単一情報源は gate_rules/）。

除外: `cp1.intentional_hold`（cp1_must_fail / scope:milestone による意図的保留）は
B1 成果物の不備ではなく CP1 判定用のシグナルなので、Verify 失敗として扱わない。
"""
from __future__ import annotations

import sys

from issuesmith.gate_rules import GATE_REGISTRY

_GATES = ("cp1", "b1_ac_format", "b1_migration")
_EXCLUDED_RULE_IDS = frozenset({"cp1.intentional_hold"})


def collect_violations(body: str, labels: list[str]):
    violations = []
    for gate in _GATES:
        violations.extend(GATE_REGISTRY[gate]().check(body, labels))
    return [v for v in violations if v.rule_id not in _EXCLUDED_RULE_IDS]


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
    from ghdag.github_client import GitHubClient

    issue_number = int(sys.argv[1])
    data = GitHubClient().issue_get(issue_number, fields=["body", "labels"])
    body = data["body"] or ""
    labels = [label["name"] for label in data.get("labels", [])]

    violations = collect_violations(body, labels)
    sys.stdout.write(format_report(violations))
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
