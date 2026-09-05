"""ac_contract.py — 受け入れ条件 YAML 契約（paths_must_exist 等）の抽出と実行。

Issue body の ## 受け入れ条件 セクション内 ```yaml ブロックを契約として解釈し、
paths_must_exist / paths_must_not_exist / references_must_resolve を
リポジトリ実体に対して検証する。

呼び出し元:
- scripts/milestone-postcheck.py（CLI・スキーマ検証付き）
- issuesmith.m2_gate（M2 クローズゲート・fail-open）
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from issuesmith.config import get_config

# リポジトリ（worktree）ルート — 互換のためモジュール定数として公開
REPO_ROOT = get_config().root
_PATH_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".toml", ".md", ".sh")


def _git_log(repo_root: Path, path: str) -> str:
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "-3", "--", path],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            return ""
        return (r.stdout or "").strip()
    except OSError:
        return ""


def _looks_like_file_path(value: str) -> bool:
    if "/" in value or value.startswith("."):
        return True
    return value.endswith(_PATH_SUFFIXES)


def _resolve_reference_path(repo_root: Path, value: str, source_file: str) -> bool:
    candidates = [
        repo_root / value,
        repo_root / "scripts" / value,
    ]
    if "schedule.yaml" in source_file and not value.startswith("/"):
        candidates.insert(0, repo_root / "scripts" / value)
    for candidate in candidates:
        if candidate.exists():
            return True
    return False


def extract_key_path_values(data: Any, key_path: str) -> list[Any]:
    """key_path の * を 1 階層のみ展開して値を収集する。"""
    parts = key_path.split(".")
    current: list[Any] = [data]

    for part in parts:
        next_values: list[Any] = []
        for item in current:
            if part == "*":
                if isinstance(item, dict):
                    next_values.extend(item.values())
                elif isinstance(item, list):
                    next_values.extend(item)
            elif isinstance(item, dict) and part in item:
                next_values.append(item[part])
        current = next_values

    flattened: list[Any] = []
    for value in current:
        if isinstance(value, list):
            flattened.extend(value)
        else:
            flattened.append(value)
    return flattened


def extract_contract_from_body(body: str) -> dict | None:
    """## 受け入れ条件 セクション内の最初の ```yaml ブロックを抽出してパースする。

    `## 7. 受け入れ条件（Acceptance Criteria）` 等の変形ヘッダにも対応する。
    契約として解釈できない場合は None。
    """
    match = re.search(
        r"^##[^#\n]*受け入れ条件[^\n]*\n(.*?)(?=^##[^#]|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return None
    section = match.group(1)
    yaml_match = re.search(r"^```yaml\n(.*?)\n```", section, re.DOTALL | re.MULTILINE)
    if not yaml_match:
        return None
    try:
        data = yaml.safe_load(yaml_match.group(1))
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def run_checks(contract: dict, repo_root: Path, *, base_ref: str = "HEAD") -> list[dict]:
    records: list[dict] = []

    post_merge = contract.get("post_merge")
    if post_merge is not None:
        if not isinstance(post_merge, list):
            records.append(
                {
                    "check": "post_merge_schema",
                    "path": "post_merge",
                    "result": "FAIL",
                    "detail": "post_merge must be a list",
                    "git_log": "",
                }
            )
        else:
            for i, item in enumerate(post_merge):
                if not isinstance(item, dict) or not isinstance(item.get("kind"), str):
                    records.append(
                        {
                            "check": "post_merge_schema",
                            "path": f"post_merge[{i}]",
                            "result": "FAIL",
                            "detail": "each post_merge item must be a dict with kind: str",
                            "git_log": "",
                        }
                    )

    for path in contract.get("paths_must_exist", []):
        target = repo_root / path
        if target.exists():
            records.append(
                {
                    "check": "paths_must_exist",
                    "path": path,
                    "result": "PASS",
                    "detail": "",
                    "git_log": "",
                }
            )
        else:
            records.append(
                {
                    "check": "paths_must_exist",
                    "path": path,
                    "result": "FAIL",
                    "detail": "file not found",
                    "git_log": _git_log(repo_root, path),
                }
            )

    for pattern in contract.get("paths_must_not_exist", []):
        matches = sorted(repo_root.glob(pattern))
        if not matches:
            records.append(
                {
                    "check": "paths_must_not_exist",
                    "path": pattern,
                    "result": "PASS",
                    "detail": "",
                    "git_log": "",
                }
            )
        else:
            rel_paths = [str(m.relative_to(repo_root)) for m in matches]
            for rel in rel_paths:
                records.append(
                    {
                        "check": "paths_must_not_exist",
                        "path": rel,
                        "result": "FAIL",
                        "detail": f"glob matched: {pattern}; matched: {', '.join(rel_paths)}",
                        "git_log": _git_log(repo_root, rel),
                    }
                )

    for ref in contract.get("references_must_resolve", []):
        source = ref["file"]
        key_path = ref["key_path"]
        source_path = repo_root / source
        if not source_path.exists():
            records.append(
                {
                    "check": "references_must_resolve",
                    "path": source,
                    "result": "FAIL",
                    "detail": f"source file not found: {source}",
                    "git_log": _git_log(repo_root, source),
                }
            )
            continue

        data = yaml.safe_load(source_path.read_text(encoding="utf-8"))
        values = extract_key_path_values(data, key_path)
        path_values = [v for v in values if isinstance(v, str) and _looks_like_file_path(v)]

        if not path_values:
            continue

        for value in path_values:
            if _resolve_reference_path(repo_root, value, source):
                records.append(
                    {
                        "check": "references_must_resolve",
                        "path": f"{source}#{key_path}={value}",
                        "result": "PASS",
                        "detail": "",
                        "git_log": "",
                    }
                )
            else:
                records.append(
                    {
                        "check": "references_must_resolve",
                        "path": f"{source}#{key_path}={value}",
                        "result": "FAIL",
                        "detail": f"reference not found: {value}",
                        "git_log": _git_log(repo_root, value),
                    }
                )

    for tree in contract.get("removed_trees", []):
        result = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", base_ref, tree],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        tracked = [line for line in (result.stdout or "").strip().splitlines() if line]
        records.append(
            {
                "check": "removed_trees",
                "path": tree,
                "result": "PASS" if not tracked else "FAIL",
                "detail": (
                    ""
                    if not tracked
                    else f"{len(tracked)} files remain (e.g. {tracked[0]})"
                ),
                "git_log": "",
            }
        )

    return records


def contract_failures(body: str, repo_root: Path | None = None) -> list[str]:
    """Issue body の契約を実行し、FAIL 記録を人間可読の文字列で返す。

    契約ブロックが無い場合は空リスト（fail-open）。
    """
    contract = extract_contract_from_body(body)
    if contract is None:
        return []
    records = run_checks(contract, repo_root or REPO_ROOT)
    return [
        f"{r['check']}: {r['path']} — {r['detail'] or 'FAIL'}"
        for r in records
        if r["result"] == "FAIL"
    ]
