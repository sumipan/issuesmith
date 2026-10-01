"""ac_contract.py — AC YAML 契約（paths_must_exist 等）の抽出と実行。

Issue body の AC セクション内 ```yaml ブロックを契約として解釈し、
paths_must_exist / paths_must_not_exist / references_must_resolve を
リポジトリ実体に対して検証する。

呼び出し元:
- scripts/milestone-postcheck.py（CLI・スキーマ検証付き）
- issuesmith.m2_gate（M2 クローズゲート・fail-open）
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml

from issuesmith.config import get_config


class GateMaterializationError(RuntimeError):
    """origin/base temporary worktree could not be materialized."""


def _git(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, check=False)


def materialize_gate_root(repo_cwd: Path, base_branch: str, prefix: str) -> Path:
    """Create a detached worktree at ``origin/<base_branch>`` for contract checks."""
    gate_root = Path(tempfile.mkdtemp(prefix=prefix))
    fetch = _git(["git", "fetch", "-q", "origin", base_branch], cwd=repo_cwd)
    if fetch.returncode != 0:
        shutil.rmtree(gate_root, ignore_errors=True)
        raise GateMaterializationError(
            f"fetch failed (repo={repo_cwd}): "
            f"fetch_rc={fetch.returncode} fetch_stderr={fetch.stderr!r}"
        )

    max_retry = 3
    backoff = [5, 10, 15]
    add: subprocess.CompletedProcess[str] | None = None
    for attempt in range(max_retry):
        subprocess.run(["git", "worktree", "prune"], cwd=str(repo_cwd), capture_output=True)
        add = _git(
            ["git", "worktree", "add", "--detach", "-q", str(gate_root), f"origin/{base_branch}"],
            cwd=repo_cwd,
        )
        if add.returncode == 0:
            return gate_root
        shutil.rmtree(gate_root, ignore_errors=True)
        if attempt < max_retry - 1:
            time.sleep(backoff[attempt])

    raise GateMaterializationError(
        f"could not materialize origin/{base_branch} (repo={repo_cwd}) "
        f"after {max_retry} attempts: "
        f"add_rc={add.returncode if add else None} add_stderr={add.stderr if add else None!r}"
    )


def cleanup_gate_root(repo_cwd: Path, gate_root: Path) -> None:
    """Remove a temporary gate worktree created by :func:`materialize_gate_root`."""
    remove = _git(["git", "worktree", "remove", "--force", str(gate_root)], cwd=repo_cwd)
    if remove.returncode != 0:
        shutil.rmtree(gate_root, ignore_errors=True)


@contextmanager
def dual_gate_roots(
    primary_repo_cwd: Path,
    secondary_repo_cwd: Path,
    base_branch: str,
    *,
    primary_prefix: str = "gate-primary-",
    secondary_prefix: str = "gate-secondary-",
) -> Iterator[tuple[Path, Path]]:
    """Materialize both roots and always cleanup, even on exceptions."""
    primary_root = materialize_gate_root(primary_repo_cwd, base_branch, primary_prefix)
    secondary_root: Path | None = None
    try:
        secondary_root = materialize_gate_root(
            secondary_repo_cwd, base_branch, secondary_prefix
        )
    except GateMaterializationError:
        cleanup_gate_root(primary_repo_cwd, primary_root)
        raise
    try:
        yield primary_root, secondary_root
    finally:
        cleanup_gate_root(primary_repo_cwd, primary_root)
        if secondary_root is not None:
            cleanup_gate_root(secondary_repo_cwd, secondary_root)

# リポジトリ（worktree）ルート — 互換のためモジュール定数として公開
REPO_ROOT = get_config().root
_PATH_SUFFIXES = (".py", ".yaml", ".yml", ".json", ".toml", ".md", ".sh")

# post_merge kinds and the required keys per kind. Checked early by B1's
# b1_migration.post_merge_schema. stable_install / tag / restart match the
# ops/preflight.check_post_merge handlers; manual_check is a human verification
# step that M2 lists as pending instead of failing (nexus #3945).
KNOWN_POST_MERGE_KINDS: frozenset[str] = frozenset(
    {"stable_install", "tag", "restart", "manual_check"}
)
POST_MERGE_REQUIRED_FIELDS: dict[str, list[str]] = {
    "stable_install": ["repo", "path"],
    "tag": ["repo", "tag"],
    "restart": ["processes"],
    "manual_check": ["description"],
}
MANUAL_CHECK_DESCRIPTION_ERROR = "kind manual_check: description must be a non-empty string"


def manual_check_description(item: dict) -> str | None:
    """Return the description of a manual_check item, or None when it is missing/empty."""
    description = item.get("description")
    if not isinstance(description, str) or not description.strip():
        return None
    return description


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


def normalize_reference_entry(ref: Any) -> tuple[str | None, str | None, str | None]:
    """Normalize one references_must_resolve entry.

    Returns (file, key_path, error_kind).
    error_kind is None (valid), 'symbol_form' (path::symbol), or 'invalid'.
    """
    if isinstance(ref, str):
        if not ref:
            return None, None, "invalid"
        if "::" in ref:
            return None, None, "symbol_form"
        return ref, None, None
    if isinstance(ref, dict):
        extra = set(ref.keys()) - {"file", "key_path"}
        if extra:
            return None, None, "invalid"
        file_val = ref.get("file")
        key_path = ref.get("key_path")
        if not isinstance(file_val, str) or not file_val:
            return None, None, "invalid"
        if key_path is not None and (not isinstance(key_path, str) or not key_path):
            return None, None, "invalid"
        if "::" in file_val:
            return None, None, "symbol_form"
        return file_val, key_path, None
    return None, None, "invalid"


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
    """AC セクション内の最初の ```yaml ブロックを抽出してパースする。

    番号付き・括弧付きの変形ヘッダ（例: ``## 7. AC（Acceptance Criteria）``）にも対応する。
    契約として解釈できない場合は None。
    """
    heading = get_config().sections["acceptance_criteria"]
    match = re.search(
        rf"^##[^#\n]*{re.escape(heading)}[^\n]*\n(.*?)(?=^##[^#]|\Z)",
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
                    detail = "each post_merge item must be a dict with kind: str"
                elif item["kind"] not in KNOWN_POST_MERGE_KINDS:
                    detail = (
                        f"unknown kind: {item['kind']}; allowed: "
                        + ", ".join(POST_MERGE_REQUIRED_FIELDS)
                    )
                elif item["kind"] == "manual_check":
                    description = manual_check_description(item)
                    if description is None:
                        detail = MANUAL_CHECK_DESCRIPTION_ERROR
                    else:
                        # Not a failure: listed as a pending manual verification
                        records.append(
                            {
                                "check": "post_merge_manual_check",
                                "path": f"post_merge[{i}]",
                                "result": "MANUAL",
                                "detail": description,
                                "git_log": "",
                            }
                        )
                        continue
                else:
                    continue
                records.append(
                    {
                        "check": "post_merge_schema",
                        "path": f"post_merge[{i}]",
                        "result": "FAIL",
                        "detail": detail,
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
        source, key_path, error_kind = normalize_reference_entry(ref)
        if error_kind == "symbol_form":
            records.append(
                {
                    "check": "references_must_resolve",
                    "path": str(ref),
                    "result": "FAIL",
                    "detail": (
                        "unsupported reference form (path::symbol);"
                        " use a file path or {file, key_path}"
                    ),
                    "git_log": "",
                }
            )
            continue
        if error_kind is not None:
            records.append(
                {
                    "check": "references_must_resolve",
                    "path": str(ref),
                    "result": "FAIL",
                    "detail": f"invalid reference entry (expected str or {{file, key_path}}): {ref!r}",
                    "git_log": "",
                }
            )
            continue
        if source is None:
            records.append(
                {
                    "check": "references_must_resolve",
                    "path": str(ref),
                    "result": "FAIL",
                    "detail": f"invalid reference entry (expected str or {{file, key_path}}): {ref!r}",
                    "git_log": "",
                }
            )
            continue
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

        if not key_path:
            records.append(
                {
                    "check": "references_must_resolve",
                    "path": source,
                    "result": "PASS",
                    "detail": "",
                    "git_log": "",
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


def pending_manual_checks(records: list[dict]) -> list[str]:
    """Return the descriptions of the post_merge manual_check items in run_checks records."""
    return [r["detail"] for r in records if r["check"] == "post_merge_manual_check"]


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
