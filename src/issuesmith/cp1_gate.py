"""
cp1_gate.py — CP1 パターン検査ゲート（薄ラッパ）

検査ロジックは gate_rules.cp1.Cp1Rules に委譲する。
"""

from __future__ import annotations

import json
import sys

from ghdag.github_client import GitHubClient

from issuesmith.gate_rules.b1_migration import B1MigrationRules
from issuesmith.gate_rules.cp1 import Cp1Rules


def check_gate(body: str, labels: list[str] | None = None) -> dict:
    """CP1 ゲートの判定結果を返す。

    scope:migration の Issue には migration 決定論ルール
    （手順・実行時状態調査・移行検証テスト契約）も CP1 で強制する。
    B1 preflight は advisory（B1 は不備で失敗しない）ため、
    機械ブロックの enforcement point はここになる。

    Returns:
        {"status": "PASS"|"FAIL", "reasons": list[str], "intentional_hold": bool}
    """
    label_list = labels or []
    violations = Cp1Rules().check(body, label_list)
    if "scope:migration" in label_list:
        violations = violations + B1MigrationRules().check(body, label_list)
    reasons = [v.message for v in violations]
    intentional_hold = any(v.rule_id == "cp1.intentional_hold" for v in violations)
    return {
        "status": "FAIL" if violations else "PASS",
        "reasons": reasons,
        "intentional_hold": intentional_hold,
    }


def main() -> None:
    """CLI: python -m issuesmith cp1-gate <issue_number>"""
    issue_number = int(sys.argv[1])

    data = GitHubClient().issue_get(issue_number, fields=["body", "labels"])
    body = data["body"]
    labels = [label["name"] for label in data.get("labels", [])]

    gate_result = check_gate(body, labels)
    print(json.dumps(gate_result))
