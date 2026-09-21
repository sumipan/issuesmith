"""CP1/B1 scope coupling gate (#3520).

Checks that allow_paths covers all files that need to be updated when
the Issue's changes are applied. Catches the class of accident where a
caller or test file is left out of allow_paths, causing the implementation
LLM to skip it (as happened in #3505 and #3507).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.ac_contract import extract_contract_from_body
from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import extract_change_table_rows
from issuesmith.steps.scope_gate import parse_allow_paths_from_ctx, resolve_scope_root

# Python builtins and common verbs that generate too many false-positive grep hits.
_DEFAULT_IGNORE_SYMBOLS: frozenset[str] = frozenset({
    "get", "set", "add", "new", "run", "try", "use", "has", "can", "put", "pop",
    "map", "key", "log", "dir", "str", "int", "any", "all", "not", "and", "or",
    "for", "if", "do", "is", "in", "to", "on", "of", "as", "at", "by", "up",
    "def", "cls", "self", "args", "kwargs", "true", "false", "none", "type",
    "list", "dict", "bool", "init", "next", "iter", "len", "max", "min",
    "open", "read", "write", "send", "main", "test", "call", "end", "raw",
    "base", "path", "file", "data", "line", "item", "name", "node", "root",
    "copy", "deep", "lock", "wait", "stop", "exit", "skip", "done", "load",
    "save", "find", "make", "build", "check", "parse", "create", "update",
    "delete", "merge", "apply", "error", "value", "result", "output", "input",
    "count", "index", "start", "close", "reset", "fetch", "order", "state",
    "issue", "label", "body", "repo", "step", "gate", "rule", "hook", "task",
    "queue", "config", "engine", "model", "forge", "token", "cache", "event",
    "level", "stage", "phase", "scope", "limit", "batch", "block", "match",
    "entry", "field", "table", "class", "raise", "yield", "await", "async",
    "import", "return", "assert", "except", "lambda", "global", "local",
    "append", "extend", "remove", "insert", "format", "encode", "decode",
    "split", "strip", "lower", "upper", "replace", "search", "compile",
    "object", "module", "package", "version", "release", "commit", "branch",
    "merge", "rebase", "status", "report", "record", "metric", "trace",
    "verify", "validate", "extract", "convert", "register", "dispatch",
    "collect", "process", "execute", "resolve", "compute", "measure",
})

# Matches backtick-quoted identifiers of 4+ chars (function names, class names, etc.)
_BACKTICK_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{3,})`")

_DELETE_MOVE_KEYWORDS: tuple[str, ...] = ("削除", "移動", "リネーム", "delete", "move", "rename")


def _parse_allow_paths(metadata: dict) -> list[str]:
    raw = metadata.get("allow_paths")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p is not None and str(p).strip()]
    if isinstance(raw, str):
        return parse_allow_paths_from_ctx(raw)
    return []


def _basename_no_ext(path: str) -> str:
    return Path(path).stem


def _is_test_path(path: str) -> bool:
    parts = Path(path).parts
    return any(p.startswith("test") for p in parts)


def _is_valid_key(key: str, extra_ignore: tuple[str, ...]) -> bool:
    if len(key) <= 3:
        return False
    kl = key.lower()
    if kl in _DEFAULT_IGNORE_SYMBOLS:
        return False
    if kl in {s.lower() for s in extra_ignore}:
        return False
    return True


def _git_grep(root: Path, pattern: str, pathspec: str) -> list[str]:
    """Return relative file paths matching pattern under root/pathspec."""
    cmd = ["git", "-C", str(root), "grep", "-rl", "--fixed-strings", "--", pattern]
    if pathspec:
        cmd.append(pathspec)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _in_allow_paths(file_path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if file_path == pat:
            return True
        if fnmatch.fnmatch(file_path, pat):
            return True
    return False


def _extract_search_keys(
    body: str,
    metadata: dict,
    root: Path,
    extra_ignore: tuple[str, ...],
) -> set[str]:
    keys: set[str] = set()
    target_repo = (metadata.get("target_repo") or "").strip()

    # Basenames of non-test allow_paths entries (test files have no callers to search for)
    for p in _parse_allow_paths(metadata):
        if _is_test_path(p):
            continue
        base = _basename_no_ext(p)
        if _is_valid_key(base, extra_ignore):
            keys.add(base)

    # Basenames of delete/move/rename rows in change table
    for repo, path, change_type in extract_change_table_rows(body):
        if repo and target_repo and repo != target_repo:
            continue
        ct_lower = change_type.lower()
        if any(kw in ct_lower for kw in _DELETE_MOVE_KEYWORDS):
            base = _basename_no_ext(path)
            if _is_valid_key(base, extra_ignore):
                keys.add(base)

    # Basenames of paths_must_not_exist (these are being deleted)
    contract = extract_contract_from_body(body) or {}
    for raw in contract.get("paths_must_not_exist") or []:
        path = str(raw).strip()
        if path:
            base = _basename_no_ext(path)
            if _is_valid_key(base, extra_ignore):
                keys.add(base)

    # Backtick-quoted identifiers that have a def/class in the target repo
    for m in _BACKTICK_IDENT_RE.finditer(body):
        ident = m.group(1)
        if not _is_valid_key(ident, extra_ignore):
            continue
        if _git_grep(root, f"def {ident}", "") or _git_grep(root, f"class {ident}", ""):
            keys.add(ident)

    return keys


def _yaml_list(paths: list[str]) -> str:
    return "\n".join(f"  - {p}" for p in paths)


class ScopeCouplingRules:
    def __init__(self) -> None:
        self.autofix_note: str | None = None
        self.autofix_new_allow_paths: list[str] | None = None

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        self.autofix_note = None
        self.autofix_new_allow_paths = None

        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []

        allow_paths = _parse_allow_paths(metadata)
        if not allow_paths:
            return []

        root = resolve_scope_root(metadata, get_config())
        if root is None:
            return []

        cfg = get_config()
        extra_ignore = cfg.scope_coupling.ignore_symbols

        keys = _extract_search_keys(body, metadata, root, extra_ignore)
        if not keys:
            return []

        test_hits: set[str] = set()
        src_hits: set[str] = set()
        for key in sorted(keys):
            for f in _git_grep(root, key, "tests"):
                test_hits.add(f)
            for f in _git_grep(root, key, "src"):
                src_hits.add(f)

        missing_tests = sorted(f for f in test_hits if not _in_allow_paths(f, allow_paths))
        missing_srcs = sorted(f for f in src_hits if not _in_allow_paths(f, allow_paths))

        if not missing_tests and not missing_srcs:
            return []

        all_missing = missing_tests + missing_srcs
        max_files = cfg.scope_gate.max_files
        merged = list(allow_paths)
        for f in all_missing:
            if f not in merged:
                merged.append(f)
        can_widen = len(merged) <= max_files

        if can_widen:
            self.autofix_new_allow_paths = merged
            self.autofix_note = _format_autofix_note(allow_paths, merged, all_missing)

        violations: list[Violation] = []
        if missing_tests:
            violations.append(
                Violation(
                    rule_id="scope_coupling.tests_outside_allow_paths",
                    severity="fail",
                    message=(
                        "allow_paths は変更に追従が必要なテストを含んでいません: "
                        + ", ".join(missing_tests)
                    ),
                    location=None,
                    auto_fixable=can_widen,
                    fix_hint=_yaml_list(missing_tests),
                )
            )
        if missing_srcs:
            violations.append(
                Violation(
                    rule_id="scope_coupling.callers_outside_allow_paths",
                    severity="fail",
                    message=(
                        "allow_paths は変更に追従が必要な呼び出し元を含んでいません: "
                        + ", ".join(missing_srcs)
                    ),
                    location=None,
                    auto_fixable=can_widen,
                    fix_hint=_yaml_list(missing_srcs),
                )
            )
        return violations


def _format_autofix_note(
    old: list[str],
    new: list[str],
    added: list[str],
) -> str:
    added_text = ", ".join(f"`{p}`" for p in added) or "(none)"
    return (
        "## CP1: allow_paths に不足ファイルを自動追加しました\n"
        "\n"
        f"**理由**: allow_paths が変更に追従が必要なファイルを含んでいません。"
        "決定論で検出した不足ファイルを追加しました。\n"
        "\n"
        f"- 追加されたファイル: {added_text}\n"
        f"- 変更後 allow_paths ファイル数: {len(new)}\n"
    )


GATE_REGISTRY["scope_coupling"] = ScopeCouplingRules
