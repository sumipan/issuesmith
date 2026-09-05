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
from issuesmith.targets import targets_from_issue

__all__ = [
    "has_acceptance_criteria_section",
    "get_unchecked_count",
    "check_gate",
    "check_gate_multi_root",
    "synthesize_contract_failures",
    "main",
]

_OR_CHECKS = frozenset({"paths_must_exist", "references_must_resolve"})


def _fmt_failure(prefix: str, record: dict) -> str:
    detail = record.get("detail") or "FAIL"
    return f"({prefix}) {record['check']}: {record['path']} — {detail}"


def synthesize_contract_failures(
    records_by_root: dict[str, list[dict]],
) -> list[str]:
    """複数 root の run_checks 結果を合成して人間可読の failure 文字列を返す。

    合成ルール:
    - paths_must_exist / references_must_resolve: いずれかの root で充足すれば充足（OR）
    - paths_must_not_exist: 全 root で不在のときのみ充足（AND）
    """
    all_records: list[tuple[str, dict]] = []
    for prefix, records in records_by_root.items():
        for record in records:
            all_records.append((prefix, record))

    failures: list[str] = []

    or_keys = {
        (r["check"], r["path"])
        for _, r in all_records
        if r["check"] in _OR_CHECKS
    }
    by_root_key: dict[tuple[str, str, str], dict] = {}
    for prefix, record in all_records:
        key = (prefix, record["check"], record["path"])
        by_root_key[key] = record

    for check, path in sorted(or_keys):
        passes = False
        root_failures: list[str] = []
        for prefix, records in records_by_root.items():
            record = by_root_key.get((prefix, check, path))
            if record is None:
                continue
            if record["result"] == "PASS":
                passes = True
                break
            if record["result"] == "FAIL":
                root_failures.append(_fmt_failure(prefix, record))
        if not passes:
            failures.extend(root_failures)

    for prefix, record in all_records:
        if record["check"] == "paths_must_not_exist" and record["result"] == "FAIL":
            failures.append(_fmt_failure(prefix, record))

    return failures


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


def check_gate_multi_root(
    body: str,
    labels: list[str],
    repo_roots: dict[str, Path],
    *,
    contracts: dict[str, dict] | None = None,
) -> dict:
    """ターゲットごとの契約を multi-root 合成して M2 ゲート判定する。

    contracts を省略した場合は body 全体を単一契約として各 root に適用する（後方互換）。
    """
    has_migration_label = "scope:migration" in labels
    violations = M2Rules().check(body, labels)
    rule_ids = {v.rule_id for v in violations}

    if "m2.ac_section_missing" in rule_ids:
        base = _proceed_or_contract_retry(body, False, has_migration_label)
    elif "m2.unchecked_ac" not in rule_ids:
        base = _proceed_or_contract_retry(body, True, has_migration_label)
    elif has_migration_label:
        return {
            "action": "migrate",
            "unchecked_count": get_unchecked_count(body),
            "has_section": True,
            "has_migration_label": True,
            "contract_failures": [],
        }
    else:
        return {
            "action": "retry",
            "unchecked_count": get_unchecked_count(body),
            "has_section": True,
            "has_migration_label": False,
            "contract_failures": [],
        }

    if base["action"] == "migrate":
        return base
    if base["action"] == "retry" and not base.get("contract_failures"):
        return base

    if contracts is None:
        from issuesmith.ac_contract import extract_contract_from_body

        contract = extract_contract_from_body(body)
        if contract is None:
            return base
        contracts = {repo: contract for repo in repo_roots}

    records_by_root: dict[str, list[dict]] = {}
    for repo, root in repo_roots.items():
        contract = contracts.get(repo, {})
        if not contract:
            records_by_root[repo] = []
            continue
        from issuesmith.ac_contract import run_checks

        records_by_root[repo] = run_checks(contract, Path(root))

    failures = synthesize_contract_failures(records_by_root)
    result = dict(base)
    result["contract_failures"] = failures
    result["action"] = "proceed" if not failures else "retry"
    return result


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


def _parse_repo_roots(values: list[str]) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for item in values:
        if "=" not in item:
            raise ValueError(f"--repo-roots の形式は repo=path です: {item!r}")
        repo, path = item.split("=", 1)
        repo = repo.strip()
        if not repo:
            raise ValueError(f"--repo-roots の repo が空です: {item!r}")
        roots[repo] = Path(path)
    return roots


def main() -> None:
    """CLI entry point: python -m issuesmith gate m2 <issue_number> [--repo-roots ...]"""
    parser = argparse.ArgumentParser(prog="m2-gate")
    parser.add_argument("issue_number", type=int)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="(非推奨) 受け入れ条件の相対パスを解決する単一ルート",
    )
    parser.add_argument(
        "--repo-roots",
        nargs="+",
        default=None,
        metavar="REPO=PATH",
        help="ターゲットごとの repo-root（例: sumipan/nexus=/path/to/nexus）",
    )
    args = parser.parse_args()

    data = GitHubClient().issue_get(args.issue_number, fields=["body", "labels"])
    body = data["body"]
    labels = [label["name"] for label in data["labels"]]

    if args.repo_roots:
        from ghdag.github_client import DEFAULT_REPO

        from issuesmith.context_hook import parse_issue_metadata

        repo_roots = _parse_repo_roots(args.repo_roots)
        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            metadata = {}
        issue_repo = str(metadata.get("issue_repo", DEFAULT_REPO))
        target_list = targets_from_issue(metadata, issue_repo=issue_repo, body=body)
        contracts = {t.repo: t.contract for t in target_list}
        gate_result = check_gate_multi_root(
            body,
            labels,
            repo_roots,
            contracts=contracts,
        )
    else:
        gate_result = check_gate(body, labels, repo_root=args.repo_root)
    print(json.dumps(gate_result))
