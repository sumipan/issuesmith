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
from issuesmith.contract import change_paths_for_repo, extract_change_table_rows
from issuesmith.scope_gate import parse_allow_paths_from_ctx, resolve_scope_root

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
    "readme", "changelog", "license", "makefile", "pyproject", "setup",
    "skill", "agents", "claude", "cursor", "codex",
})

# Common file names (extension included) that are never useful as grep keys (#4076).
_IGNORE_FILENAMES: frozenset[str] = frozenset({
    "README.md", "CHANGELOG.md", "LICENSE", "Makefile",
    "pyproject.toml", "__init__.py", "SKILL.md",
})

# Matches backtick-quoted identifiers of 4+ chars (function names, class names, etc.)
_BACKTICK_IDENT_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{3,})`")

# Move / rename verbs are not part of the language pack; the pack's delete words are added
# at call time (see _delete_move_keywords).
_MOVE_KEYWORDS: tuple[str, ...] = ("move", "rename")

# Data / config file extensions whose modification requires the tests that pin their contents
# (nexus #3949).
_DATA_FILE_EXTS: frozenset[str] = frozenset({".yml", ".yaml", ".json", ".toml", ".txt"})

# Matches ${identifier} template variable syntax inside backticks.
_BACKTICK_TEMPLATE_VAR_RE = re.compile(r"`\$\{([A-Za-z_][A-Za-z0-9_]*)\}`")



def _removal_keywords() -> tuple[str, ...]:
    """Removal / replacement verbs (language pack ``removal_words``), lower-cased.

    They mark removal context in headings, table rows and replacement lines.
    """
    return tuple(w.lower() for w in get_config().language.removal_words)


def _delete_move_keywords() -> tuple[str, ...]:
    """Change-type words of delete / move / rename rows, lower-cased."""
    delete_words = tuple(w.lower() for w in get_config().language.delete_words)
    return delete_words + _MOVE_KEYWORDS

# Matches key:value tokens within backticks (e.g., `mode: iterative`).
_BACKTICK_KEY_VALUE_RE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_]*\s*:\s*[A-Za-z0-9_]+)`"
)


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
    if key in _IGNORE_FILENAMES:
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


def _git_grep_tests_py(
    root: Path, pattern: str, *, word_boundary: bool = False
) -> list[str]:
    """Return .py files under tests/ matching pattern."""
    flags = ["-rl"]
    if word_boundary:
        flags.append("-w")
    flags.append("--fixed-strings")
    cmd = ["git", "-C", str(root), "grep"] + flags + ["--", pattern, "tests/"]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        return []
    return [
        line.strip()
        for line in proc.stdout.splitlines()
        if line.strip() and line.strip().endswith(".py")
    ]


def _extract_replacement_targets(body: str) -> tuple[set[str], set[str]]:
    """Return (word_boundary_idents, exact_strings) from replacement/deletion context.

    Scans lines containing a removal keyword (:func:`_removal_keywords`).  Backtick-quoted
    key:value tokens (e.g. ``mode: iterative``) are added to exact_strings; their
    values are also added to word_boundary_idents.  Plain identifier tokens are
    added to word_boundary_idents.
    """
    wb: set[str] = set()
    exact: set[str] = set()
    keywords = _removal_keywords()
    for line in body.splitlines():
        line_lower = line.lower()
        if not any(kw in line_lower for kw in keywords):
            continue
        for m in _BACKTICK_KEY_VALUE_RE.finditer(line):
            token = m.group(1).strip()
            exact.add(token)
            val = re.split(r"\s*:\s*", token, maxsplit=1)[-1].strip()
            if val and _is_valid_key(val):
                wb.add(val)
        for m in _BACKTICK_IDENT_RE.finditer(line):
            ident = m.group(1)
            if _is_valid_key(ident):
                wb.add(ident)
    return wb, exact


def _in_allow_paths(file_path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if file_path == pat:
            return True
        if fnmatch.fnmatch(file_path, pat):
            return True
    return False


def _removal_names(body: str) -> set[str]:
    """Return backtick identifiers and template variable names from removal context.

    Removal context is: table rows whose cells contain a removal keyword, or headings
    whose text contains a removal keyword (the heading text and the body under it).
    """
    names: set[str] = set()

    def _extract_from_text(text: str) -> None:
        for m in _BACKTICK_IDENT_RE.finditer(text):
            names.add(m.group(1))
        for m in _BACKTICK_TEMPLATE_VAR_RE.finditer(text):
            names.add(m.group(1))

    removal_keywords = _removal_keywords()
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
            if any(kw in row_lower for kw in removal_keywords):
                _extract_from_text(line)
            continue

        # Heading detection
        heading_match = re.match(r"^(#{2,4})\s+(.*)", line)
        if heading_match:
            if in_removal_section:
                _flush_section_body()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).lower()
            if any(kw in heading_text for kw in removal_keywords):
                # The heading itself may name the target (e.g. "## Deprecate `FOO`").
                _extract_from_text(heading_match.group(2))
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
    data_file_tests: bool = True,
) -> tuple[set[str], set[str]]:
    """Return ``(required, optional)`` search keys (sumipan/nexus#3527).

    required — derived from declarations: basenames of deleted / moved files (change table,
        ``paths_must_not_exist``), file names with extension of modified data / config files
        (``_DATA_FILE_EXTS``, when ``data_file_tests``; nexus #3949) and public symbols named in the body whose definition lives in
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
    delete_move_keywords = _delete_move_keywords()
    for repo, path, change_type in extract_change_table_rows(body):
        if repo and target_repo and repo != target_repo:
            continue
        changed_files.add(path)
        ct_lower = change_type.lower()
        if any(kw in ct_lower for kw in delete_move_keywords):
            base = _basename_no_ext(path)
            if _is_valid_key(base):
                required.add(base)
        elif data_file_tests and Path(path).suffix.lower() in _DATA_FILE_EXTS:
            # Tests may pin the file's contents (e.g. an EXPECTED dict of every row).
            name = Path(path).name
            if _is_valid_key(name):
                required.add(name)

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


# ---------------------------------------------------------------------------
# paths_must_not_exist contract validity (#4257)
# ---------------------------------------------------------------------------

PATHS_MUST_NOT_EXIST_RULE_ID = "scope_coupling.paths_must_not_exist_unjustified"



def _current_sub_re() -> re.Pattern[str]:
    """``<sub_header_prefix>N <derived_from_phrase>`` in a child body (group 1 = N)."""
    language = get_config().language
    return re.compile(
        re.escape(language.sub_header_prefix)
        + r"(\d+)\s+"
        + re.escape(language.derived_from_phrase)
    )


def _parent_number_re() -> re.Pattern[str]:
    """``<parent_issue_label>: #N`` line of a child body (group 1 = N)."""
    return re.compile(
        "^" + re.escape(get_config().language.parent_issue_label) + r":\s*#(\d+)",
        re.MULTILINE,
    )


def _tracked_path_exists(repo_path: Path, base_branch: str, path: str) -> bool:
    """Return True when ``path`` is tracked at ``base_branch`` in ``repo_path``."""
    for ref in (f"origin/{base_branch}", base_branch, "HEAD"):
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "cat-file", "-e", f"{ref}:{path}"],
            capture_output=True,
            check=False,
        )
        if proc.returncode == 0:
            return True
    return False


def _deleted_paths_in_body(body: str, target_repo: str) -> set[str]:
    """Paths declared as deletion rows in the Issue body's change table(s)."""
    delete_words = [w.lower() for w in get_config().scope_size.delete_words]
    deleted: set[str] = set()
    for repo, path, change_type in extract_change_table_rows(body):
        if repo and target_repo and repo != target_repo:
            continue
        ct_lower = change_type.lower()
        if any(w in ct_lower for w in delete_words):
            deleted.add(path)
    return deleted


def _current_sub_number(body: str) -> int | None:
    match = _current_sub_re().search(body)
    return int(match.group(1)) if match else None


def _body_with_sub_blocks(body: str) -> str | None:
    """Return a body that contains milestone sub blocks (self or fetched parent)."""
    from issuesmith.gate_rules.b1_milestone_subdesign import extract_sub_blocks

    if extract_sub_blocks(body):
        return body
    parent_match = _parent_number_re().search(body)
    if not parent_match:
        return None
    try:
        from ghdag.forge import get_forge

        parent = get_forge().issue_get(int(parent_match.group(1)), fields=["body"])
        parent_body = parent.get("body") or ""
        return parent_body if extract_sub_blocks(parent_body) else None
    except Exception:
        return None


def _sibling_change_path_owners(body: str, target_repo: str) -> dict[str, int]:
    """Map change-table paths to the sub number that owns them in a milestone design."""
    from issuesmith.gate_rules.b1_milestone_subdesign import extract_sub_blocks

    parent_like = _body_with_sub_blocks(body)
    if not parent_like:
        return {}
    owners: dict[str, int] = {}
    for sub_num, block in extract_sub_blocks(parent_like):
        for path in change_paths_for_repo(block, target_repo or None):
            owners[path] = sub_num
    return owners


def _sibling_owner_sub(
    referrer: str,
    sibling_paths: dict[str, int],
    current_sub: int | None,
) -> int | None:
    owner = sibling_paths.get(referrer)
    if owner is None:
        return None
    if current_sub is not None and owner == current_sub:
        return None
    return owner


def check_paths_must_not_exist_contract(
    body: str,
    repo_path: Path,
    *,
    base_branch: str = "main",
) -> list[Violation]:
    """Reject ``paths_must_not_exist`` entries this Issue cannot satisfy (#4257).

    Each path is allowed when it appears as a deletion row in the change table,
    or when it is absent on the base branch checkout.
    """
    contract = extract_contract_from_body(body) or {}
    raw_paths = contract.get("paths_must_not_exist") or []
    must_not_exist = sorted(
        {str(p).strip() for p in raw_paths if str(p).strip()}
    )
    if not must_not_exist:
        return []

    try:
        metadata = parse_issue_metadata(body)
    except Exception:
        metadata = {}
    target_repo = (metadata.get("target_repo") or "").strip()
    branch = str(metadata.get("base_branch") or base_branch).strip() or base_branch
    deleted = _deleted_paths_in_body(body, target_repo)

    violations: list[Violation] = []
    for path in must_not_exist:
        if path in deleted:
            continue
        if not _tracked_path_exists(repo_path, branch, path):
            continue
        violations.append(
            Violation(
                rule_id=PATHS_MUST_NOT_EXIST_RULE_ID,
                severity="fail",
                message=(
                    f"paths_must_not_exist lists `{path}` but this Issue does not "
                    "delete it and the path exists on the base branch"
                ),
                location=path,
                auto_fixable=False,
                fix_hint=(
                    "add a deletion row for this path in the change table, remove it "
                    "from paths_must_not_exist, or move it to a sibling sub-issue "
                    "that deletes it"
                ),
            )
        )
    return violations


# ---------------------------------------------------------------------------
# Deletion reference check (nexus #3953)
# ---------------------------------------------------------------------------

DELETION_RULE_ID = "scope_coupling.deletion_reference_uncovered"

# Directories whose files break when a referenced file is deleted but are not covered by
# type checks / CI of the runtime code.
DELETION_SEARCH_DIRS: tuple[str, ...] = ("src", "tests", "scripts", "tools")


def _module_parts_from_path(path: str) -> tuple[str, ...]:
    """Return module name parts, stripping leading 'src' for src-layout repos."""
    parts = Path(path).with_suffix("").parts
    if parts and parts[0] == "src" and len(parts) >= 3:
        return parts[1:]
    return parts


def _deletion_search_pathspecs(deleted_path: str) -> tuple[str, ...]:
    """Return grep pathspecs for a deleted path, including its parent directory."""
    parent = str(Path(deleted_path).parent)
    specs = list(DELETION_SEARCH_DIRS)
    if parent and parent not in (".", "") and parent not in specs:
        specs.append(parent)
    return tuple(specs)


def _stem_is_unique_in_repo(stem: str, repo_path: Path) -> bool:
    """Return True if exactly one tracked file in repo_path has this stem."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            return False
        return sum(1 for f in proc.stdout.splitlines() if Path(f).stem == stem) == 1
    except Exception:
        return False


def _filename_is_unique_in_repo(name: str, repo_path: Path) -> bool:
    """Return True if exactly one tracked file in repo_path has this basename."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "ls-files"],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            return False
        return sum(1 for f in proc.stdout.splitlines() if Path(f).name == name) == 1
    except Exception:
        return False


def _deletion_search_key_kinds(
    path: str, repo_path: Path | None = None
) -> list[tuple[str, str]]:
    """Return ``(key, kind)`` pairs for a deleted path (duplicate keys removed, order preserved)."""
    name = Path(path).name
    if name in _IGNORE_FILENAMES:
        return []

    kinds: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(key: str, kind: str) -> None:
        if key not in seen:
            seen.add(key)
            kinds.append((key, kind))

    if _is_valid_key(name):
        if repo_path is None or _filename_is_unique_in_repo(name, repo_path):
            add(name, "name")

    parts = _module_parts_from_path(path)
    if len(parts) > 1:
        add(".".join(parts), "module")
        add(path, "path")
        raw_parts = Path(path).with_suffix("").parts
        if raw_parts and raw_parts[0] == "src" and len(parts) > 2:
            add(".".join(parts[:-1]), "module")

    if Path(path).suffix == ".py" and repo_path is not None:
        stem = Path(path).stem
        if _is_valid_key(stem) and _stem_is_unique_in_repo(stem, repo_path):
            add(stem, "stem")
            stem_under = stem.replace("-", "_")
            if stem_under != stem:
                add(stem_under, "stem")

    return kinds


def deletion_search_keys(path: str, repo_path: Path | None = None) -> list[str]:
    """Return grep keys for a deleted path (duplicates removed).

    Always includes: file name (filtered by ``_is_valid_key``), plus module-path
    (dot-separated, e.g. ``tools.mltgnt_bridge.progress``) and slash-path
    (e.g. ``tools/mltgnt_bridge/progress.py``) when the file is in a subdirectory.

    Adds bare stem and its dash→underscore variant only when ``repo_path`` is given,
    the path is ``.py``, and the stem is unique across all tracked files (#4165).
    Without ``repo_path`` the stem is omitted on the safe side.
    """
    return [key for key, _kind in _deletion_search_key_kinds(path, repo_path)]


def _git_grep_lines(root: Path, pattern: str, pathspec: str) -> list[tuple[str, str]]:
    """Return ``(relative_path, line_body)`` from ``git grep -n --fixed-strings``."""
    cmd = ["git", "-C", str(root), "grep", "-n", "--fixed-strings", "--", pattern]
    if pathspec:
        cmd.append(pathspec)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        return []
    rows: list[tuple[str, str]] = []
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        rel, _, line_body = raw.partition(":")
        if not rel or not line_body:
            continue
        _, _, content = line_body.partition(":")
        rows.append((rel.strip(), content if content else line_body))
    return rows


def _deleted_parent_module(deleted_path: str) -> str:
    parts = _module_parts_from_path(deleted_path)
    if len(parts) <= 1:
        return parts[0] if parts else ""
    return ".".join(parts[:-1])


_DYNAMIC_IMPORT_FUNCS: tuple[str, ...] = ("spec_from_file_location",)


def _stem_import_list_pattern(key: str) -> re.Pattern[str]:
    escaped = re.escape(key)
    return re.compile(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])")


def _stem_match_context(
    key: str,
    line: str,
    deleted_path: str,
    referrer_path: str,
) -> str | None:
    """Return stem reference context (import/file/dynamic) or None for non-references."""
    deleted = Path(deleted_path)
    deleted_dir = str(deleted.parent)
    referrer_dir = str(Path(referrer_path).parent)
    parent_module = _deleted_parent_module(deleted_path)
    escaped_key = re.escape(key)
    import_token = _stem_import_list_pattern(key)

    if deleted_dir == referrer_dir:
        if re.search(rf"(?<![A-Za-z0-9_])import\s+{escaped_key}\b", line):
            return "import"
        relative = re.search(r"from\s+\.\s+import\s+(.+)", line)
        if relative and import_token.search(relative.group(1)):
            return "import"

    from_match = re.search(r"from\s+([A-Za-z_][A-Za-z0-9_.]*)\s+import\s+(.+)", line)
    if from_match and from_match.group(1) == parent_module and import_token.search(from_match.group(2)):
        return "import"

    deleted_name = deleted.name
    if re.search(
        rf"(?<![A-Za-z0-9_.\-]){re.escape(deleted_name)}(?![A-Za-z0-9_])",
        line,
    ):
        return "file"
    if deleted_path in line:
        return "file"

    if any(fn in line for fn in _DYNAMIC_IMPORT_FUNCS):
        if re.search(rf"""['"]{escaped_key}['"]""", line):
            return "dynamic"
        if deleted_name in line or deleted_path in line:
            return "dynamic"

    return None


def _key_matches_line(key: str, kind: str, line: str) -> bool:
    if kind == "path":
        return True
    escaped = re.escape(key)
    if kind == "module":
        pattern = rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
    elif kind == "name":
        pattern = rf"(?<![A-Za-z0-9_.\-]){escaped}(?![A-Za-z0-9_])"
    elif kind == "stem":
        pattern = rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
    else:
        return False
    return re.search(pattern, line) is not None


def _deleted_paths(body: str, target_repo: str) -> list[str]:
    # Same change-type vocabulary as scope_size (host config, e.g. nexus adds its own word).
    delete_words = [w.lower() for w in get_config().scope_size.delete_words]
    paths: list[str] = []
    for repo, path, change_type in extract_change_table_rows(body):
        if repo and target_repo and repo != target_repo:
            continue
        ct_lower = change_type.lower()
        if any(w in ct_lower for w in delete_words) and path not in paths:
            paths.append(path)
    return paths


def _deletion_hits(
    body: str,
    allow_paths: list[str],
    repo_path: Path,
) -> tuple[
    dict[str, list[tuple[str, str, str, str]]],
    dict[str, list[tuple[str, int]]],
]:
    """Map each deleted path to uncovered referrers and sibling-owned referrers."""
    try:
        metadata = parse_issue_metadata(body)
    except Exception:
        metadata = {}
    target_repo = (metadata.get("target_repo") or "").strip()
    deleted = _deleted_paths(body, target_repo)
    if not deleted:
        return {}, {}

    contract = extract_contract_from_body(body) or {}
    must_not_exist = sorted(
        {str(p).strip() for p in (contract.get("paths_must_not_exist") or []) if str(p).strip()}
    )
    covered = list(allow_paths) + must_not_exist
    sibling_paths = _sibling_change_path_owners(body, target_repo)
    current_sub = _current_sub_number(body)

    uncovered: dict[str, list[tuple[str, str, str, str]]] = {}
    sibling_handled: dict[str, list[tuple[str, int]]] = {}
    for path in deleted:
        by_referrer: dict[str, tuple[str, str, str, str]] = {}
        by_sibling: dict[str, tuple[str, int]] = {}
        for key, kind in _deletion_search_key_kinds(path, repo_path):
            for d in _deletion_search_pathspecs(path):
                for rel_path, line in _git_grep_lines(repo_path, key, d):
                    if rel_path in deleted or _in_allow_paths(rel_path, covered):
                        continue
                    if not _key_matches_line(key, kind, line):
                        continue
                    effective_kind = kind
                    if kind == "stem":
                        stem_context = _stem_match_context(key, line, path, rel_path)
                        if stem_context is None:
                            continue
                        effective_kind = f"stem:{stem_context}"
                    owner_sub = _sibling_owner_sub(rel_path, sibling_paths, current_sub)
                    if owner_sub is not None:
                        if rel_path not in by_sibling:
                            by_sibling[rel_path] = (rel_path, owner_sub)
                        continue
                    if rel_path not in by_referrer:
                        by_referrer[rel_path] = (rel_path, key, effective_kind, line)
        if by_referrer:
            uncovered[path] = sorted(by_referrer.values(), key=lambda row: row[0])
        if by_sibling:
            sibling_handled[path] = sorted(by_sibling.values(), key=lambda row: row[0])
    return uncovered, sibling_handled


def uncovered_deletion_references(
    body: str,
    allow_paths: list[str],
    repo_path: Path,
) -> dict[str, list[str]]:
    """Map each deleted path to its referrers outside ``allow_paths`` / ``paths_must_not_exist``.

    For every change-table row whose change type matches ``scope_size.delete_words``, ``git grep``
    the base checkout at ``repo_path`` under :data:`DELETION_SEARCH_DIRS` for the file name, stem
    and module name, then re-check each matching line with kind-specific boundary rules.
    Deleted paths without uncovered referrers are omitted.
    """
    hits, _sibling = _deletion_hits(body, allow_paths, repo_path)
    return {
        path: [referrer for referrer, _key, _kind, _line in rows]
        for path, rows in hits.items()
    }


def _deletion_violations(
    hits: dict[str, list[tuple[str, str, str, str]]],
    sibling_handled: dict[str, list[tuple[str, int]]] | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    sibling_handled = sibling_handled or {}
    for path, rows in hits.items():
        files = [referrer for referrer, _key, _kind, _line in rows]
        detail_lines = "\n".join(
            f"- {referrer}: {kind} `{key}` → {line.strip()[:120]}"
            for referrer, key, kind, line in rows
        )
        sibling_lines = sibling_handled.get(path) or []
        sibling_note = ""
        if sibling_lines:
            sibling_note = (
                "\nSibling sub handles (do not add to paths_must_not_exist on this Issue):\n"
                + "\n".join(
                    f"- `{referrer}` — sibling sub #{sub_num} handles it"
                    for referrer, sub_num in sibling_lines
                )
            )
        violations.append(
            Violation(
                rule_id=DELETION_RULE_ID,
                severity="fail",
                message=(
                    f"files referencing deleted `{path}` are in neither allow_paths nor "
                    "paths_must_not_exist: " + ", ".join(files)
                    + sibling_note
                ),
                location=path,
                auto_fixable=False,
                fix_hint=(
                    "add them to allow_paths (to update the reference) or paths_must_not_exist "
                    "(to delete the referrer too):\n"
                    + _yaml_list(files)
                    + "\n"
                    + detail_lines
                    + sibling_note
                ),
            )
        )
    return violations


def check_deletion_references(
    body: str,
    allow_paths: list[str],
    repo_path: Path,
) -> list[Violation]:
    """``scope_coupling.deletion_reference_uncovered`` — one violation per deleted path."""
    hits, sibling = _deletion_hits(body, allow_paths, repo_path)
    return _deletion_violations(hits, sibling)


def deletion_references_for_body(body: str) -> dict[str, list[str]]:
    """Resolve allow_paths / base checkout from ``body`` and run the deletion reference check.

    Used at dispatch time and after a dependency merges. Runs even when
    ``scope_coupling.enabled`` is false (that flag turns off the caller/test coupling check only).
    Returns ``{}`` when the body has no allow_paths or the target clone is missing.
    """
    cfg = get_config()
    try:
        metadata = parse_issue_metadata(body)
    except Exception:
        return {}
    allow_paths = _parse_allow_paths(metadata)
    if not allow_paths:
        return {}
    root = resolve_scope_root(metadata, cfg)
    if root is None:
        return {}
    return uncovered_deletion_references(body, allow_paths, root)


def format_deletion_references(refs: dict[str, list[str]], lead: str) -> str:
    """Markdown for Issue comments: ``lead`` + referrer list per deleted path."""
    blocks: list[str] = []
    for path, files in refs.items():
        lines = [f"{lead}the following files reference deleted `{path}`:"]
        lines.extend(f"- `{f}`" for f in files)
        blocks.append("\n".join(lines))
    blocks.append("Add them to `allow_paths` or `paths_must_not_exist`.")
    return "\n\n".join(blocks)


BEHAVIOR_PIN_RULE_ID = "scope_coupling.behavior_pinned_outside_allow_paths"
PATH_STRING_RULE_ID = "scope_coupling.path_string_outside_allow_paths"
CODE_CONSTS_PIN_RULE_ID = "scope_coupling.code_consts_pinned_outside_allow_paths"

_CODE_ATTR_PIN_RE = re.compile(r"\b([A-Za-z_]\w*)\.(?:__(?:code|defaults)__)\b")
_GETSOURCE_PIN_RE = re.compile(
    r"getsource\(\s*(?:[A-Za-z_][\w.]*\.)?([A-Za-z_]\w*)\s*\)"
)
_DEF_NAME_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\b", re.MULTILINE)


def _target_src_files_for_code_consts(
    body: str,
    allow_paths: list[str],
    root: Path,
) -> list[Path]:
    try:
        metadata = parse_issue_metadata(body)
    except Exception:
        metadata = {}
    target_repo = (metadata.get("target_repo") or "").strip()
    paths: set[str] = set()
    for p in allow_paths:
        if p.endswith(".py") and not _is_test_path(p) and "*" not in p:
            paths.add(p)
    for repo, path, _change_type in extract_change_table_rows(body):
        if repo and target_repo and repo != target_repo:
            continue
        if path.endswith(".py") and not _is_test_path(path) and "*" not in path:
            paths.add(path)
    existing: list[Path] = []
    for rel in sorted(paths):
        full = root / rel
        if full.is_file():
            existing.append(full)
    return existing


def check_code_consts_pinning(
    body: str,
    allow_paths: list[str],
    root: Path,
) -> list[str]:
    """Return test paths outside allow_paths that pin defs in changed src via __code__ etc."""
    src_files = _target_src_files_for_code_consts(body, allow_paths, root)
    if not src_files:
        return []

    defined_names: set[str] = set()
    for fp in src_files:
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _DEF_NAME_RE.finditer(text):
            defined_names.add(m.group(1))
    if not defined_names:
        return []

    cmd = [
        "git",
        "-C",
        str(root),
        "grep",
        "-n",
        "-E",
        r"__code__|__defaults__|getsource\(",
        "--",
        "tests/",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode not in (0, 1):
        return []

    hits: set[str] = set()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        rel_path, content = parts[0], parts[2]
        if not rel_path.endswith(".py"):
            continue
        pinned: set[str] = set()
        for m in _CODE_ATTR_PIN_RE.finditer(content):
            pinned.add(m.group(1))
        for m in _GETSOURCE_PIN_RE.finditer(content):
            pinned.add(m.group(1))
        if pinned & defined_names:
            hits.add(rel_path)

    return sorted(f for f in hits if not _in_allow_paths(f, allow_paths))


def check_behavior_pinning(
    body: str,
    allow_paths: list[str],
    root: Path,
) -> list[Violation]:
    """Detect test files outside allow_paths that pin deleted/replaced symbols.

    Extracts backtick-quoted identifiers and key:value tokens from lines that
    contain deletion/replacement keywords, then searches tests/**/*.py for those
    patterns.  Hits outside allow_paths are reported as a single Violation.
    """
    wb_keys, exact_keys = _extract_replacement_targets(body)
    if not wb_keys and not exact_keys:
        return []
    hits: set[str] = set()
    for key in sorted(wb_keys):
        hits.update(_git_grep_tests_py(root, key, word_boundary=True))
    for key in sorted(exact_keys):
        hits.update(_git_grep_tests_py(root, key))
    uncovered = sorted(f for f in hits if not _in_allow_paths(f, allow_paths))
    if not uncovered:
        return []
    return [
        Violation(
            rule_id=BEHAVIOR_PIN_RULE_ID,
            severity="fail",
            message=(
                "tests outside allow_paths pin deleted/replaced symbols: "
                + ", ".join(uncovered)
            ),
            location=None,
            auto_fixable=False,
            fix_hint="add these test files to allow_paths:\n" + _yaml_list(uncovered),
        )
    ]


def check_allow_paths_string_references(
    allow_paths: list[str],
    root: Path,
) -> list[Violation]:
    """Detect tests outside allow_paths that reference allow_paths entries as path strings.

    For each non-test, non-glob entry in allow_paths, searches tests/**/*.py for
    the exact path string.  One Violation per path that has uncovered referrers.
    """
    violations: list[Violation] = []
    for path in allow_paths:
        if _is_test_path(path) or "*" in path:
            continue
        hits = _git_grep_tests_py(root, path)
        uncovered = sorted(f for f in hits if not _in_allow_paths(f, allow_paths))
        if not uncovered:
            continue
        violations.append(
            Violation(
                rule_id=PATH_STRING_RULE_ID,
                severity="fail",
                message=(
                    f"tests outside allow_paths reference path `{path}`: "
                    + ", ".join(uncovered)
                ),
                location=path,
                auto_fixable=False,
                fix_hint="add these test files to allow_paths:\n" + _yaml_list(uncovered),
            )
        )
    return violations


class ScopeCouplingRules:
    def __init__(self) -> None:
        self.autofix_note: str | None = None
        self.autofix_new_allow_paths: list[str] | None = None

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        self.autofix_note = None
        self.autofix_new_allow_paths = None

        cfg = get_config()
        if not cfg.scope_coupling.enabled:
            # ``enabled: false`` turns off the caller/test coupling check only; deleted-file
            # referrers are always checked (nexus #3953).
            try:
                metadata = parse_issue_metadata(body)
            except Exception:
                return []
            allow_paths = _parse_allow_paths(metadata)
            if not allow_paths:
                return []
            root = resolve_scope_root(metadata, cfg)
            if root is None:
                return []
            hits, sibling = _deletion_hits(body, allow_paths, root)
            violations = _deletion_violations(hits, sibling)
            violations.extend(check_paths_must_not_exist_contract(body, root))
            return violations

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

        violations = self._coupling_violations(body, metadata, allow_paths, root, cfg)
        base = self.autofix_new_allow_paths or allow_paths
        uncovered_cc = check_code_consts_pinning(body, base, root)
        if uncovered_cc:
            merged = base + [u for u in uncovered_cc if u not in base]
            can_widen_cc = len(merged) <= cfg.scope_gate.max_files
            if can_widen_cc:
                self.autofix_new_allow_paths = merged
                self.autofix_note = _format_autofix_note(
                    allow_paths,
                    merged,
                    [p for p in merged if p not in allow_paths],
                )
            violations.append(
                Violation(
                    rule_id=CODE_CONSTS_PIN_RULE_ID,
                    severity="fail",
                    message=(
                        "tests outside allow_paths pin functions via "
                        "__code__/__defaults__/getsource: "
                        + ", ".join(uncovered_cc)
                    ),
                    location=None,
                    auto_fixable=can_widen_cc,
                    fix_hint=(
                        "add these test files to allow_paths:\n"
                        + _yaml_list(uncovered_cc)
                    ),
                )
            )
        # Referrers already added by the autofix widening are covered (nexus #3953).
        effective = self.autofix_new_allow_paths or allow_paths
        hits, sibling = _deletion_hits(body, effective, root)
        violations.extend(_deletion_violations(hits, sibling))
        violations.extend(check_paths_must_not_exist_contract(body, root))
        violations.extend(check_behavior_pinning(body, effective, root))
        violations.extend(check_allow_paths_string_references(effective, root))
        return violations

    def _coupling_violations(
        self,
        body: str,
        metadata: dict,
        allow_paths: list[str],
        root: Path,
        cfg,
    ) -> list[Violation]:
        # scope_mode: internal — the Issue declares its public interface unchanged, so callers
        # and tests need no follow-up. P2 verifies the declaration (public symbol set == base).
        if str(metadata.get("scope_mode") or "").strip().lower() == "internal":
            return []

        required, optional = _extract_search_keys(
            body, metadata, root, data_file_tests=cfg.scope_coupling.data_file_tests
        )
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
                        "allow_paths is missing tests that must follow this change: "
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
                        "allow_paths is missing callers that must follow this change: "
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
    return get_config().language.message(
        "scope_coupling.autofix_note", added=added_text, count=len(new)
    )


GATE_REGISTRY["scope_coupling"] = ScopeCouplingRules
