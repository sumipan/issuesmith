"""
cp1_gate.py — CP1 パターン検査ゲート（薄ラッパ）

検査ロジックは gate_rules.cp1.Cp1Rules に委譲する。
"""

from __future__ import annotations

import json
import sys

from ghdag.forge import get_forge

from issuesmith.gate_rules.b1_migration import B1MigrationRules
from issuesmith.gate_rules.cp1 import Cp1Rules
from issuesmith.gate_rules.milestone_consistency import MilestoneConsistencyRules
from issuesmith.gate_rules.scope_breadth import ScopeBreadthRules


def check_gate(body: str, labels: list[str] | None = None) -> dict:
    """CP1 ゲートの判定結果を返す。

    scope:migration の Issue には migration 決定論ルール
    （手順・実行時状態調査・移行検証テスト契約）も CP1 で強制する。
    B1 preflight は advisory（B1 は不備で失敗しない）ため、
    機械ブロックの enforcement point はここになる。

    分割計画パターンと scope:milestone の矛盾はラベル有無に依存せず常に検査する
    （ラベルが無いこと自体が違反のため条件付きスキップは不可）。

    allow_paths が幅ゲートを超過していても、変更対象ファイル表 + paths_must_exist
    から決定論で絞り込める場合 ScopeBreadthRules.check は violation を出さず続行する
    （#3487）。絞り込みが起きたときは autofix_note / autofix_new_allow_paths に
    書き換え内容が入る（Issue body の実際の永続化は main() が forge 経由で行う）。

    Returns:
        {"status": "PASS"|"FAIL", "reasons": list[str], "intentional_hold": bool,
         "autofix_note": str | None, "autofix_new_allow_paths": list[str] | None}
    """
    label_list = labels or []
    scope_rule = ScopeBreadthRules()
    violations = Cp1Rules().check(body, label_list)
    violations = violations + MilestoneConsistencyRules().check(body, label_list)
    violations = violations + scope_rule.check(body, label_list)
    if "scope:migration" in label_list:
        violations = violations + B1MigrationRules().check(body, label_list)
    reasons = [v.message for v in violations]
    intentional_hold = any(v.rule_id == "cp1.intentional_hold" for v in violations)
    return {
        "status": "FAIL" if violations else "PASS",
        "reasons": reasons,
        "intentional_hold": intentional_hold,
        "autofix_note": scope_rule.autofix_note,
        "autofix_new_allow_paths": scope_rule.autofix_new_allow_paths,
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
