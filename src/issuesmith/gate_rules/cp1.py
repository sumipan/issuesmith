from __future__ import annotations

import posixpath
import re
import shlex
import subprocess
from pathlib import Path

import yaml

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[misc, assignment]
from ghdag.workflow.gates import GATE_REGISTRY, Violation
from ghdag.workflow.gates.common import strip_code_regions

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata, validate_issue_metadata
from issuesmith.contract import extract_change_table_rows
from issuesmith.gate_rules.b1_ac_format import extract_yaml_block, get_ac_section
from issuesmith.gate_rules.b1_milestone_subdesign import (
    _extract_paths_from_change_table,
    extract_sub_blocks,
)
from issuesmith.scope_gate import resolve_scope_root

_YAML_CONTRACT_FIXES: dict[str, tuple[bool, str]] = {
    "missing_required": (
        True,
        "add `target_repo: sumipan/nexus` (nexus itself) or"
        " `target_repo: sumipan/<repo>` to the leading YAML block",
    ),
    "annotation_in_path": (
        True,
        "remove parenthesized notes (`(...)`) from allow_paths and list file paths only",
    ),
    "invalid_path_format": (
        True,
        "remove paths starting with `/var/tmp/` from allow_paths",
    ),
}
_YAML_CONTRACT_DEFAULT_FIX = (False, "set target_repo to the matching repository")
# Parent ACs about the milestone process itself (not covered by any sub AC).
_META_AC_PATTERN = re.compile(
    r"(PR\s*#\d+"
    r"|child\s+issues?\s+(?:are\s+)?(?:created|filed)"
    r"|close\s+this\s+issue"
    r"|all\s+sub-?issues?\s+(?:are\s+)?implemented)",
    re.IGNORECASE,
)
_OPTIONAL_AC_PATTERN = re.compile(r"^\(optional\)", re.IGNORECASE)
# Token separators: ASCII ones plus the CJK symbols / punctuation, katakana middle dot
# and fullwidth punctuation ranges (named escapes keep this file free of CJK literals).
_KEYWORD_TOKEN_SEP_RE = re.compile(
    r"[\s:()/\N{IDEOGRAPHIC SPACE}-\N{IDEOGRAPHIC HALF FILL SPACE}"
    r"\N{KATAKANA MIDDLE DOT}\N{FULLWIDTH EXCLAMATION MARK}-\N{FULLWIDTH SOLIDUS}"
    r"\N{FULLWIDTH COLON}-\N{FULLWIDTH COMMERCIAL AT}]"
)
_VERSION_ASSIGN_LINE = re.compile(r"^\s*version\s*=")
_VERSION_EXACT_ASSERT = re.compile(r'version["\]\s]*\s*==\s*["\'][0-9]+\.[0-9]+')
_GIT_PIN_EXACT = re.compile(r"git\+https://[^\"']*@v[0-9]+\.[0-9]")
_COUNT_EQ_ONE = re.compile(r"count\s*\(.*\)\s*==\s*1")
_TEST_VERSION_ASSERT_HINT = (
    "check versions / pins with a lower bound (`>=`) or do not test them;"
    " publish bumps them deterministically"
)


def _is_tests_path(path: str) -> bool:
    normalized = path.strip().lstrip("b/").lstrip("a/")
    return normalized.startswith("tests/") or "/tests/" in normalized


def check_version_line_in_diff(diff: str) -> list[Violation]:
    """Reject an LLM unified diff that changes pyproject.toml ``version =`` (#2766).

    ``scripts/issuesmith-version-bump.py`` bumps the version deterministically, so the
    path for the implementing LLM to rewrite the version line is closed structurally.
    """
    in_pyproject = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_pyproject = bool(re.search(r"[ab]/(?:.+/)?pyproject\.toml\b", line))
            continue
        if line.startswith("+++ b/"):
            in_pyproject = line[6:].rstrip().endswith("pyproject.toml")
            continue
        if not in_pyproject:
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line[:1] not in "+-":
            continue
        if _VERSION_ASSIGN_LINE.match(line[1:]):
            return [
                Violation(
                    rule_id="cp1.version_line_in_diff",
                    severity="fail",
                    message=(
                        "the LLM diff changes the pyproject.toml version = line;"
                        " issuesmith-version-bump.py bumps the version deterministically"
                    ),
                    location="pyproject.toml",
                    auto_fixable=False,
                    fix_hint=(
                        "drop the version = line change from the diff;"
                        " the publish step applies the bump automatically"
                    ),
                )
            ]
    return []


def check_test_version_exact_assert(diff: str) -> list[Violation]:
    """Detect exact version / pin asserts under tests/ from unified diff + lines (#3065).

    Pattern a: ``version == "X.Y.Z"`` / ``project["version"] == "..."``
    Pattern b: ``git+https://...@vX.Y.Z`` checked with ``==`` / ``count(...) == 1``
    """
    in_tests = False
    file_has_count_eq_one = False
    plus_git_pin = False

    def _violation() -> list[Violation]:
        return [
            Violation(
                rule_id="cp1.test_version_exact_assert",
                severity="fail",
                message=(
                    "tests/ contains an exact version or git pin assert;"
                    " the deterministic publish bump breaks it in cascade"
                ),
                location="tests/",
                auto_fixable=False,
                fix_hint=_TEST_VERSION_ASSERT_HINT,
            )
        ]

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_tests = bool(re.search(r"[ab]/tests/", line))
            file_has_count_eq_one = False
            plus_git_pin = False
            continue
        if line.startswith("+++ b/"):
            in_tests = _is_tests_path(line[6:])
            continue
        if not in_tests:
            continue
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if not line or line[0] not in " +-":
            continue

        content = line[1:]
        if _COUNT_EQ_ONE.search(content):
            file_has_count_eq_one = True

        if line[0] != "+":
            if plus_git_pin and file_has_count_eq_one:
                return _violation()
            continue

        if _VERSION_EXACT_ASSERT.search(content):
            return _violation()

        has_git_pin = bool(_GIT_PIN_EXACT.search(content))
        if has_git_pin:
            plus_git_pin = True
            if "==" in content or _COUNT_EQ_ONE.search(content):
                return _violation()

        if plus_git_pin and file_has_count_eq_one:
            return _violation()

    if plus_git_pin and file_has_count_eq_one:
        return _violation()
    return []


def _yaml_contract_fix(code: str) -> tuple[bool, str]:
    return _YAML_CONTRACT_FIXES.get(code, _YAML_CONTRACT_DEFAULT_FIX)


def _check_scope_gate_hard_max(metadata: dict) -> list[Violation]:
    """Reject Issue YAML scope_gate.max_files above config hard_max_files (#3349)."""
    raw = metadata.get("scope_gate")
    if not isinstance(raw, dict) or "max_files" not in raw:
        return []
    try:
        max_files = int(raw["max_files"])
    except (TypeError, ValueError):
        return [
            Violation(
                rule_id="cp1.yaml_contract.scope_gate_over_hard_max",
                severity="fail",
                message="scope_gate.max_files is not an integer",
                location="scope_gate.max_files",
                auto_fixable=True,
                fix_hint=(
                    "set scope_gate.max_files to an integer <= "
                    f"{get_config().scope_gate.hard_max_files}"
                ),
            )
        ]
    hard = get_config().scope_gate.hard_max_files
    if max_files <= hard:
        return []
    return [
        Violation(
            rule_id="cp1.yaml_contract.scope_gate_over_hard_max",
            severity="fail",
            message=(
                f"scope_gate.max_files ({max_files}) exceeds "
                f"hard_max_files ({hard})"
            ),
            location="scope_gate.max_files",
            auto_fixable=True,
            fix_hint=f"set scope_gate.max_files to {hard} or less",
        )
    ]


def _allow_paths_count(metadata: dict) -> int:
    allow_paths_raw = metadata.get("allow_paths", [])
    if isinstance(allow_paths_raw, str):
        return 1
    if isinstance(allow_paths_raw, list):
        return len(allow_paths_raw)
    return 0


def _check_split(metadata: dict) -> list[Violation]:
    if "split" not in metadata:
        return []
    split = metadata.get("split")
    if split == "atomic":
        hard = get_config().scope_gate.hard_max_files
        count = _allow_paths_count(metadata)
        if count > hard:
            return [
                Violation(
                    rule_id="cp1.yaml_contract.split_atomic_over_hard_max",
                    severity="fail",
                    message=(
                        f"split: atomic Issues are limited to {hard} allow_paths "
                        f"entries (got {count})"
                    ),
                    location="allow_paths",
                    auto_fixable=False,
                    fix_hint=(
                        "split the Issue; atomic Issues are limited to "
                        "hard_max_files allow_paths entries"
                    ),
                )
            ]
        return []
    return [
        Violation(
            rule_id="cp1.yaml_contract.split_invalid",
            severity="fail",
            message=f"split must be atomic (got {split!r})",
            location="split",
            auto_fixable=False,
            fix_hint="set split to atomic or remove it",
        )
    ]


def _extract_sub_ac_section(block: str) -> str | None:
    ac = get_config().sections["acceptance_criteria"]
    match = re.search(
        rf"\*\*{re.escape(ac)}\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
        block,
        re.DOTALL,
    )
    return match.group(1) if match else None


def _extract_ac_checkbox_items(section: str) -> list[str]:
    return re.findall(r"^\s*-\s+\[[ xX]\]\s+(.*)$", section, re.MULTILINE)


def _sub_location(sub_num: int) -> str:
    return f"#### {get_config().language.sub_header_prefix}{sub_num}"


def _is_new_or_modify(change_type: str) -> bool:
    """True for a non-empty change-type cell that is not a delete word of the pack."""
    lowered = change_type.strip().lower()
    if not lowered:
        return False
    return not any(w.lower() in lowered for w in get_config().language.delete_words)


def _normalize_candidate_path(path: str) -> str:
    normalized = path.strip().strip("`")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_test_candidate_path(path: str) -> bool:
    if not path.startswith("tests/"):
        return False
    name = posixpath.basename(path)
    if name.endswith("_test.py"):
        return True
    return name.startswith("test_") and name.endswith(".py")


def _change_type_is_new(change_type: str) -> bool:
    lowered = change_type.strip().lower()
    return any(word in lowered for word in get_config().scope_size.new_words)


def _paths_must_exist_from_ac(body: str) -> list[str]:
    ac_section = get_ac_section(body)
    if ac_section is None:
        return []
    yaml_text = extract_yaml_block(ac_section)
    if yaml_text is None:
        return []
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("paths_must_exist", [])
    if not isinstance(raw, list):
        return []
    return [
        _normalize_candidate_path(item)
        for item in raw
        if isinstance(item, str) and item.strip()
    ]


def _new_test_candidates_from_body(body: str, metadata: dict) -> list[str]:
    target_repo = metadata.get("target_repo") or "sumipan/nexus"
    seen: set[str] = set()
    candidates: list[str] = []

    def add(path: str) -> None:
        normalized = _normalize_candidate_path(path)
        if not _is_test_candidate_path(normalized) or normalized in seen:
            return
        seen.add(normalized)
        candidates.append(normalized)

    for repo, path, change_type in extract_change_table_rows(body):
        if repo and repo != target_repo:
            continue
        if _change_type_is_new(change_type):
            add(path)

    for path in _paths_must_exist_from_ac(body):
        add(path)

    return candidates


def _package_dirs_from_tracked(tracked_paths: list[str]) -> set[str]:
    dirs: set[str] = set()
    for path in tracked_paths:
        if path.endswith("__init__.py"):
            parent = posixpath.dirname(path)
            if parent:
                dirs.add(parent)
    return dirs


def _package_dirs_from_change_table(body: str) -> set[str]:
    dirs: set[str] = set()
    for _, path, change_type in extract_change_table_rows(body):
        if not path.endswith("__init__.py"):
            continue
        if not _is_new_or_modify(change_type):
            continue
        parent = posixpath.dirname(path)
        if parent:
            dirs.add(parent)
    return dirs


def _pytest_module_name(path: str, package_dirs: set[str]) -> str:
    stem = Path(path).stem
    parts: list[str] = []
    directory = posixpath.dirname(path)
    while directory in package_dirs:
        parts.insert(0, posixpath.basename(directory))
        directory = posixpath.dirname(directory)
    parts.append(stem)
    return ".".join(parts)


def _uses_importlib_mode(root: Path) -> bool:
    if tomllib is None:
        return False
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    ini_options = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if not isinstance(ini_options, dict):
        return False
    addopts = ini_options.get("addopts")
    if addopts is None:
        return False
    tokens: list[str] = []
    if isinstance(addopts, str):
        tokens = shlex.split(addopts)
    elif isinstance(addopts, list):
        for item in addopts:
            if isinstance(item, str):
                tokens.extend(shlex.split(item))
    else:
        return False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("--import-mode="):
            if token.split("=", 1)[1] == "importlib":
                return True
        elif token == "--import-mode":
            if index + 1 < len(tokens) and tokens[index + 1] == "importlib":
                return True
            index += 1
        index += 1
    return False


def _tracked_paths_under_tests(root: Path) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", "tests"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


def _collision_fix_hint(candidate: str) -> str:
    parent_name = posixpath.basename(posixpath.dirname(candidate))
    stem = Path(candidate).stem
    suffix = stem.removeprefix("test_") if stem.startswith("test_") else stem
    example = f"tests/{parent_name}/test_{parent_name}_{suffix}.py"
    return f"rename it to a unique basename, e.g. {example}"


def _check_test_basename_collision(body: str, metadata: dict) -> list[Violation]:
    candidates = _new_test_candidates_from_body(body, metadata)
    if not candidates:
        return []

    root = resolve_scope_root(metadata, get_config())
    if root is None:
        return []

    if _uses_importlib_mode(root):
        return []

    tracked = _tracked_paths_under_tests(root)
    package_dirs = _package_dirs_from_tracked(tracked) | _package_dirs_from_change_table(body)

    tracked_test_py = [
        path
        for path in tracked
        if path.endswith(".py") and _is_test_candidate_path(path)
    ]

    module_by_path: dict[str, str] = {}
    for path in candidates + tracked_test_py:
        module_by_path[path] = _pytest_module_name(path, package_dirs)

    violations: list[Violation] = []
    for candidate in sorted(candidates):
        module_name = module_by_path[candidate]
        opponents = sorted(
            {
                other
                for other, other_module in module_by_path.items()
                if other != candidate and other_module == module_name
            }
        )
        if not opponents:
            continue
        violations.append(
            Violation(
                rule_id="cp1.test_basename_collision",
                severity="fail",
                message=(
                    f'new test file {candidate} collides with {", ".join(opponents)} '
                    f'as pytest module "{module_name}"'
                ),
                location=candidate,
                auto_fixable=True,
                fix_hint=_collision_fix_hint(candidate),
            )
        )
    return violations


def _keyword_tokens(text: str) -> list[str]:
    return [
        t
        for t in _KEYWORD_TOKEN_SEP_RE.split(text.strip())
        if len(t) >= 3
    ]


_CONCRETE_HINT = "replace it with a concrete description"


class Cp1Rules:
    # (pattern, rule_id, message, fix_hint). Rule ids keep their historical names.
    FAIL_PATTERNS: list[tuple[str, str, str, str]] = [
        (r"TODO:", "cp1.forbidden_word.todo", "TODO: remains", _CONCRETE_HINT),
        (r"TBD", "cp1.forbidden_word.tbd", "TBD remains", _CONCRETE_HINT),
        (
            r"(?i)\b(?:needs confirmation|to be confirmed)\b",
            "cp1.forbidden_word.youkakunin",
            '"needs confirmation" remains',
            _CONCRETE_HINT,
        ),
        (r"(?i)\bundecided\b", "cp1.forbidden_word.mitei", '"undecided" remains', _CONCRETE_HINT),
        (
            r"(?i)\bunder consideration\b",
            "cp1.forbidden_word.kentouchuu",
            '"under consideration" remains',
            _CONCRETE_HINT,
        ),
        (
            r"(?i)\b(?:confirm|check) with the user\b",
            "cp1.forbidden_word.user_confirm",
            '"confirm with the user" remains',
            _CONCRETE_HINT,
        ),
    ]

    def _parse_must_fail(self, body: str) -> bool:
        match = re.match(r"^```yaml\s*\n([\s\S]*?)```", body.lstrip())
        if not match:
            return False
        frontmatter = match.group(1)
        return bool(re.search(r"^\s*cp1_must_fail\s*:\s*true\s*$", frontmatter, re.MULTILINE))

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        violations: list[Violation] = []
        stripped = strip_code_regions(body)

        for pattern, rule_id, message, fix_hint in self.FAIL_PATTERNS:
            if re.search(pattern, stripped):
                violations.append(Violation(
                    rule_id=rule_id,
                    severity="fail",
                    message=message,
                    location=None,
                    auto_fixable=True,
                    fix_hint=fix_hint,
                ))

        if self._parse_must_fail(body):
            violations.append(Violation(
                rule_id="cp1.intentional_hold",
                severity="fail",
                message="cp1_must_fail: true is set",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            ))

        # YAML contract check. A missing or unparsable leading block fails as missing_block.
        # Skipping it would let a "draft-done without YAML" through until P0 at the
        # develop entrance stops it (#2539/#2541).
        try:
            metadata = parse_issue_metadata(body)
        except (ValueError, yaml.YAMLError) as exc:
            violations.append(Violation(
                rule_id="cp1.yaml_contract.missing_block",
                severity="fail",
                message=f"the leading yaml metadata block is missing or unparsable: {exc}",
                location=None,
                auto_fixable=True,
                fix_hint=(
                    "add a yaml block of this form at the top of the Issue body"
                    f" (derive allow_paths from the \"{get_config().sections['changed_files']}\""
                    " table; name target_repo when the target is an external repository):\n"
                    "```yaml\n"
                    "base_branch: main\n"
                    "allow_paths:\n"
                    "  - \"<changed path pattern>\"\n"
                    "```"
                ),
            ))
        else:
            for mv in validate_issue_metadata(metadata):
                auto_fixable, fix_hint = _yaml_contract_fix(mv.code)
                violations.append(Violation(
                    rule_id=f"cp1.yaml_contract.{mv.code}",
                    severity="fail",
                    message=mv.message,
                    location=mv.field,
                    auto_fixable=auto_fixable,
                    fix_hint=fix_hint,
                ))
            violations.extend(_check_scope_gate_hard_max(metadata))
            violations.extend(_check_split(metadata))
            violations.extend(_check_test_basename_collision(body, metadata))

        if "scope:milestone" in labels:
            violations.append(Violation(
                rule_id="cp1.intentional_hold",
                severity="fail",
                message="scope:milestone label is set (CP1 always fails)",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            ))
            violations.extend(self._check_milestone_sub_blocks(body))
            violations.extend(self._check_milestone_sub_ac_yaml(body))
            violations.extend(self._check_parent_ac_orphans(body))
            violations.extend(self._check_paths_must_exist_design(body))

        return violations

    def _check_milestone_sub_blocks(self, body: str) -> list[Violation]:
        violations: list[Violation] = []
        for sub_num, block in extract_sub_blocks(body):
            stripped = strip_code_regions(block)
            for pattern, rule_id, message, fix_hint in self.FAIL_PATTERNS:
                if re.search(pattern, stripped):
                    violations.append(Violation(
                        rule_id=f"{rule_id}.sub{sub_num}",
                        severity="fail",
                        message=f"Sub {sub_num}: {message}",
                        location=_sub_location(sub_num),
                        auto_fixable=True,
                        fix_hint=fix_hint,
                    ))
        return violations

    def _check_milestone_sub_ac_yaml(self, body: str) -> list[Violation]:
        violations: list[Violation] = []
        ac = get_config().sections["acceptance_criteria"]
        for sub_num, block in extract_sub_blocks(body):
            ac_section = _extract_sub_ac_section(block)
            if ac_section is None:
                violations.append(Violation(
                    rule_id="cp1.milestone.sub_ac_yaml_missing",
                    severity="fail",
                    message=f"Sub {sub_num}: no {ac} section",
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=None,
                ))
                continue
            if extract_yaml_block(ac_section) is None:
                violations.append(Violation(
                    rule_id="cp1.milestone.sub_ac_yaml_missing",
                    severity="fail",
                    message=f"Sub {sub_num}: {ac} has no ```yaml block",
                    location=_sub_location(sub_num),
                    auto_fixable=True,
                    fix_hint=f"add a paths_must_exist YAML block at the top of {ac}",
                ))
        return violations

    def _check_parent_ac_orphans(self, body: str) -> list[Violation]:
        if not extract_sub_blocks(body):
            return []
        ac_section = get_ac_section(body)
        if ac_section is None:
            return []
        parent_items = _extract_ac_checkbox_items(ac_section)
        sub_items: list[str] = []
        for _, block in extract_sub_blocks(body):
            sub_ac = _extract_sub_ac_section(block)
            if sub_ac:
                sub_items.extend(_extract_ac_checkbox_items(sub_ac))

        violations: list[Violation] = []
        ac_heading = get_config().sections["acceptance_criteria"]
        for item in parent_items:
            if _META_AC_PATTERN.search(item):
                continue
            if _OPTIONAL_AC_PATTERN.search(item):
                continue
            tokens = _keyword_tokens(item)
            if not tokens:
                continue
            covered = any(
                any(token in sub_item for token in tokens)
                for sub_item in sub_items
            )
            if not covered:
                violations.append(Violation(
                    rule_id="cp1.milestone.parent_ac_orphan",
                    severity="fail",
                    message=f"parent AC is not covered by any sub AC: {item[:80]}",
                    location=f"## {ac_heading}",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations

    def _check_paths_must_exist_design(self, body: str) -> list[Violation]:
        if not extract_sub_blocks(body):
            return []
        ac_section = get_ac_section(body)
        if ac_section is None:
            return []
        yaml_text = extract_yaml_block(ac_section)
        if yaml_text is None:
            return []
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return []
        if not isinstance(data, dict):
            return []

        paths_must_exist = data.get("paths_must_exist", [])
        if not isinstance(paths_must_exist, list):
            return []

        mapped_paths: set[str] = set()
        for _, block in extract_sub_blocks(body):
            for _, path, change_type in _extract_paths_from_change_table(block):
                if _is_new_or_modify(change_type):
                    mapped_paths.add(path)

        violations: list[Violation] = []
        ac_heading = get_config().sections["acceptance_criteria"]
        for path in paths_must_exist:
            if not isinstance(path, str):
                continue
            normalized = path.strip().strip("`")
            if normalized not in mapped_paths:
                violations.append(Violation(
                    rule_id="cp1.milestone.paths_must_exist_unmapped",
                    severity="fail",
                    message=(
                        f"paths_must_exist `{normalized}` is not listed in"
                        " any sub's new or modify change-table row"
                    ),
                    location=f"## {ac_heading}",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations


GATE_REGISTRY["cp1"] = Cp1Rules
