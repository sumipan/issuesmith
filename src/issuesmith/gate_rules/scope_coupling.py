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

# Keywords that mark removal/deprecation context in headings and table rows.
_REMOVAL_KEYWORDS: tuple[str, ...] = ("削除", "撤去", "廃止", "delete", "remove")

# Matches ${identifier} template variable syntax inside backticks.
_BACKTICK_TEMPLATE_VAR_RE = re.compile(r"`\$\{([A-Za-z_][A-Za-z0-9_]*)\}`")


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


def _is_valid_key(key: str) -> bool:
    if len(key) <= 3:
        return False
    kl = key.lower()
    if kl in _DEFAULT_IGNORE_SYMBOLS:
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


def _removal_names(body: str) -> set[str]:
    """Return backtick identifiers and template variable names from removal context.

    Removal context is: table rows whose cells contain a removal keyword, or body
    paragraphs under headings whose text contains a removal keyword.
    """
    names: set[str] = set()

    def _extract_from_text(text: str) -> None:
        for m in _BACKTICK_IDENT_RE.finditer(text):
            names.add(m.group(1))
        for m in _BACKTICK_TEMPLATE_VAR_RE.finditer(text):
            names.add(m.group(1))

    lines = body.splitlines()
    in_removal_section = False
    current_section_level = 0
    section_body_lines: list[str] = []

    def _flush_section_body() -> None:
        _extract_from_text("\n".join(section_body_lines))
        section_body_lines.clear()

    for line in lines:
        # Table rows
        if line.strip().startswith("|"):
            row_lower = line.lower()
            if any(kw in row_lower for kw in _REMOVAL_KEYWORDS):
                _extract_from_text(line)
            continue

        # Heading detection
        heading_match = re.match(r"^(#{2,4})\s+(.*)", line)
        if heading_match:
            if in_removal_section:
                _flush_section_body()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).lower()
            if any(kw in heading_text for kw in _REMOVAL_KEYWORDS):
                in_removal_section = True
                current_section_level = level
                section_body_lines.clear()
            else:
                # Close removal section when we hit same or higher level heading
                if in_removal_section and level <= current_section_level:
                    in_removal_section = False
            continue

        if in_removal_section:
            section_body_lines.append(line)

    if in_removal_section:
        _flush_section_body()

    return names


def _extract_search_keys(
    body: str,
    metadata: dict,
    root: Path,
) -> tuple[set[str], set[str]]:
    """Return ``(required, optional)`` search keys (sumipan/nexus#3527).

    required — derived from declarations: basenames of deleted / moved files (change table,
        ``paths_must_not_exist``) and public symbols named in the body whose definition lives in
        a file the Issue changes (allow_paths or change table). Their callers / tests must be in
        allow_paths.
    optional — string-match only: basenames of allow_paths entries and identifiers defined
        outside the changed files. Reported in ``fix_hint`` for reference, never a violation and
        never used to widen allow_paths (this is what made the requirement grow with allow_paths).
    """
    required: set[str] = set()
    optional: set[str] = set()
    target_repo = (metadata.get("target_repo") or "").strip()
    allow_paths = _parse_allow_paths(metadata)
    changed_files: set[str] = {p for p in allow_paths if not p.endswith("**")}

    # Basenames of non-test allow_paths entries: reference only
    for p in allow_paths:
        if _is_test_path(p):
            continue
        base = _basename_no_ext(p)
        if _is_valid_key(base):
            optional.add(base)

    # Basenames of delete/move/rename rows in change table
    for repo, path, change_type in extract_change_table_rows(body):
        if repo and target_repo and repo != target_repo:
            continue
        changed_files.add(path)
        ct_lower = change_type.lower()
        if any(kw in ct_lower for kw in _DELETE_MOVE_KEYWORDS):
            base = _basename_no_ext(path)
            if _is_valid_key(base):
                required.add(base)

    # Basenames of paths_must_not_exist (these are being deleted): declaration → required
    contract = extract_contract_from_body(body) or {}
    for raw in contract.get("paths_must_not_exist") or []:
        path = str(raw).strip()
        if path:
            base = _basename_no_ext(path)
            if _is_valid_key(base):
                required.add(base)

    # Identifiers appearing in removal-context (tables/headings): unconditionally required.
    for name in _removal_names(body):
        if _is_valid_key(name):
            required.add(name)

    # Backtick-quoted identifiers with a def/class in the target repo: required only when the
    # definition is in a file this Issue changes (its public interface may change).
    for m in _BACKTICK_IDENT_RE.finditer(body):
        ident = m.group(1)
        if not _is_valid_key(ident):
            continue
        if ident in required:
            continue
        defined_in = _git_grep(root, f"def {ident}", "") + _git_grep(root, f"class {ident}", "")
        if not defined_in:
            continue
        if any(_in_allow_paths(f, sorted(changed_files)) for f in defined_in):
            required.add(ident)
        else:
            optional.add(ident)

    return required, optional - required


def _yaml_list(paths: list[str]) -> str:
    return "\n".join(f"  - {p}" for p in paths)


class ScopeCouplingRules:
    def __init__(self) -> None:
        self.autofix_note: str | None = None
        self.autofix_new_allow_paths: list[str] | None = None

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        self.autofix_note = None
        self.autofix_new_allow_paths = None

        cfg = get_config()
        if not cfg.scope_coupling.enabled:
            return []

        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []

        allow_paths = _parse_allow_paths(metadata)
        if not allow_paths:
            return []

        root = resolve_scope_root(metadata, cfg)
        if root is None:
            target_repo = (metadata.get("target_repo") or "").strip()
            external_dir = str(cfg.paths.external_dir)
            return [
                Violation(
                    rule_id="scope_coupling.root_unavailable",
                    severity="fail",
                    message=(
                        f"scope coupling cannot be measured: "
                        f"no clone for '{target_repo}' under {external_dir}"
                    ),
                    location=None,
                    auto_fixable=False,
                    fix_hint=(
                        "clone the target repo into .claude/external/<repo> "
                        "(same layout P0 uses) and re-run the gate"
                    ),
                )
            ]

        # scope_mode: internal — the Issue declares its public interface unchanged, so callers
        # and tests need no follow-up. P2 verifies the declaration (public symbol set == base).
        if str(metadata.get("scope_mode") or "").strip().lower() == "internal":
            return []

        required, optional = _extract_search_keys(body, metadata, root)
        if not required and not optional:
            return []

        search_dirs = cfg.scope_coupling.search_dirs

        def _hits(keys: set[str]) -> tuple[set[str], set[str]]:
            tests: set[str] = set()
            srcs: set[str] = set()
            for key in sorted(keys):
                for d in search_dirs:
                    hits = _git_grep(root, key, d)
                    if d == "tests":
                        tests.update(hits)
                    else:
                        srcs.update(hits)
            return tests, srcs

        req_tests, req_srcs = _hits(required)
        opt_tests, opt_srcs = _hits(optional)

        missing_tests = sorted(f for f in req_tests if not _in_allow_paths(f, allow_paths))
        missing_srcs = sorted(f for f in req_srcs if not _in_allow_paths(f, allow_paths))
        reference = sorted(
            f
            for f in (opt_tests | opt_srcs)
            if not _in_allow_paths(f, allow_paths) and f not in missing_tests and f not in missing_srcs
        )

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

        over_note = ""
        if not can_widen:
            over_note = (
                f" (adding them would exceed scope_breadth max_files={max_files}: "
                f"{len(merged)} files; split the Issue or declare scope_mode: internal)"
            )
        ref_hint = ("\n# reference (string match only, not required):\n" + _yaml_list(reference)) if reference else ""

        # When over scope_breadth limit, violations are "warn" (visible but non-blocking)
        # instead of "fail" — the Issue is too broad to auto-extend, but that is a
        # constraint of the scope itself, not a new defect introduced by this change.
        severity = "fail" if can_widen else "warn"

        violations: list[Violation] = []
        if missing_tests:
            violations.append(
                Violation(
                    rule_id="scope_coupling.tests_outside_allow_paths",
                    severity=severity,
                    message=(
                        "allow_paths は変更に追従が必要なテストを含んでいません: "
                        + ", ".join(missing_tests)
                        + over_note
                    ),
                    location=None,
                    auto_fixable=can_widen,
                    fix_hint=_yaml_list(missing_tests) + ref_hint,
                )
            )
        if missing_srcs:
            violations.append(
                Violation(
                    rule_id="scope_coupling.callers_outside_allow_paths",
                    severity=severity,
                    message=(
                        "allow_paths は変更に追従が必要な呼び出し元を含んでいません: "
                        + ", ".join(missing_srcs)
                        + over_note
                    ),
                    location=None,
                    auto_fixable=can_widen,
                    fix_hint=_yaml_list(missing_srcs) + ref_hint,
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
