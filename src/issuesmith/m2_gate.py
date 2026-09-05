"""
m2_gate.py — M2 受け入れ条件 checkbox ゲート（薄ラッパ）

ロジックは gate_rules/m2.py の M2Rules に委譲する。
has_acceptance_criteria_section / get_unchecked_count を re-export して後方互換を維持。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ghdag.github_client import GitHubClient

from issuesmith.gate_rules.m2 import (
    M2Rules,
    get_unchecked_count,
    has_acceptance_criteria_section,
)

__all__ = ["has_acceptance_criteria_section", "get_unchecked_count", "check_gate", "main"]


def _contract_failures_failopen(body: str, repo_root: Path | None = None) -> list[str]:
    """受け入れ条件 YAML 契約を実行し FAIL を返す。実行系エラーは fail-open。"""
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
    """checkbox ゲート通過後の最終判定。YAML 契約 FAIL があれば retry に落とす。"""
    failures = _contract_failures_failopen(body, repo_root=repo_root)
    if failures:
        return {
            "action": "retry",
            "unchecked_count": 0,
            "has_section": has_section,
            "has_migration_label": has_migration_label,
            "contract_failures": failures,
        }
    return {
        "action": "proceed",
        "unchecked_count": 0,
        "has_section": has_section,
        "has_migration_label": has_migration_label,
        "contract_failures": [],
    }


def check_gate(body: str, labels: list[str], repo_root: Path | None = None) -> dict:
    """M2 ゲートの判定結果を返す。

    Args:
        body: Issue body。
        labels: Issue ラベル名のリスト。
        repo_root: 受け入れ条件 YAML の相対パス解決ルート。省略時は ac_contract 既定（nexus ルート）。

    Returns:
        dict with keys:
          - action: 'proceed' | 'migrate' | 'retry'
          - unchecked_count: int
          - has_section: bool
          - has_migration_label: bool
          - contract_failures: list[str]（受け入れ条件 YAML 契約の FAIL。checkbox 通過時のみ実行）
    """
    has_migration_label = "scope:migration" in labels
    violations = M2Rules().check(body, labels)

    rule_ids = {v.rule_id for v in violations}

    if "m2.ac_section_missing" in rule_ids:
        return _proceed_or_contract_retry(body, False, has_migration_label, repo_root=repo_root)

    if "m2.unchecked_ac" not in rule_ids:
        return _proceed_or_contract_retry(body, True, has_migration_label, repo_root=repo_root)

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


def main() -> None:
    """CLI entry point: python -m issuesmith m2-gate <issue_number> [--repo-root PATH]"""
    parser = argparse.ArgumentParser(prog="m2-gate")
    parser.add_argument("issue_number", type=int)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="受け入れ条件の相対パスを解決するルート（省略時は nexus ルート）",
    )
    args = parser.parse_args()

    data = GitHubClient().issue_get(args.issue_number, fields=["body", "labels"])
    body = data["body"]
    labels = [label["name"] for label in data["labels"]]

    gate_result = check_gate(body, labels, repo_root=args.repo_root)
    print(json.dumps(gate_result))
