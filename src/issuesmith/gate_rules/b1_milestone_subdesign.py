from __future__ import annotations

import re
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata, parse_issue_metadata_blocks
from issuesmith.contract import (  # noqa: F401 — re-exported for legacy importers
    SUB_HEADER_RE,
    _normalize_path,
    change_paths_for_repo,
    extract_change_table_rows,
    get_section,
    iter_sub_blocks,
    parse_table_rows,
)

_SUB_HEADER_RE = SUB_HEADER_RE
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
_FILE_REF_RE = re.compile(r"`([^`]+\.[a-zA-Z0-9]+)`|(?:^|[\s(/])([\w./-]+\.[a-zA-Z0-9]+)")

# Sub design text announcing that the sub fails the tests on its own (#4518).
# src/ must stay CJK-free (tests/test_no_cjk_src.py) and the language pack has no field for
# these phrases yet, so only the EN wording is matched.
_STANDALONE_FAIL_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:this\s+sub|sub\s*\d+)\s+(?:alone|on\s+its\s+own|standalone)\b"
        r"[^\n.]*?(?:ImportError|\btests?\b|\bcollect)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:full|whole|entire)\s+test\s+(?:suite\s+)?pass\b[^\n.]*\bparent\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btests?\s+(?:are\s+|is\s+)?(?:followed|updated|fixed|handled)\s+(?:up\s+)?"
        r"(?:in|by)\s+(?:another|a\s+sibling|a\s+separate|a\s+different)\s+sub\b",
        re.IGNORECASE,
    ),
)
# Acceptance criteria wording that requires the whole test suite to pass.
_FULL_SUITE_AC_RE = re.compile(
    r"\b(?:existing\s+tests|all\s+tests|full\s+test\s+suite|tests?\s+pass|collection)\b",
    re.IGNORECASE,
)


def _sub_location(sub_num: int) -> str:
    """``#### <sub_header_prefix>N`` location of a sub design block."""
    return f"#### {get_config().language.sub_header_prefix}{sub_num}"


def extract_sub_blocks(body: str) -> list[tuple[int, str]]:
    design = get_section(body, get_config().sections["design"])
    return iter_sub_blocks(design or "")


def _count_sub_plan_rows(body: str) -> int | None:
    sections = get_config().sections
    milestone = get_section(body, sections["milestone"])
    if not milestone:
        return None
    plan = sections["sub_plan"]
    plan_match = re.search(
        rf"###\s+{re.escape(plan)}\s*\n(.*?)(?=^###|\Z)",
        milestone,
        re.MULTILINE | re.DOTALL,
    )
    if not plan_match:
        return None
    rows = parse_table_rows(plan_match.group(1))
    if len(rows) <= 1:
        return 0
    data_rows = 0
    for row in rows[1:]:
        if row and row[0].strip().isdigit():
            data_rows += 1
    return data_rows


# Canonical extractors live in issuesmith.contract (R1). Aliases keep old names importable.
_extract_paths_from_table_section = extract_change_table_rows
_extract_paths_from_change_table = extract_change_table_rows


def _extract_parent_change_paths(body: str) -> set[str]:
    section = get_section(body, get_config().sections["changed_files"])
    if not section:
        return set()
    paths = {path for _, path, _ in _extract_paths_from_table_section(section)}
    return paths


def _allowed_repos(body: str) -> set[str]:
    """Union of every metadata block's target_repo (+ diary) — one block per repo (#4076)."""
    repos: set[str] = set()
    for metadata in parse_issue_metadata_blocks(body):
        target_repo = metadata.get("target_repo")
        if isinstance(target_repo, str) and target_repo.strip():
            repos.add(target_repo.strip())
        if metadata.get("diary_allow_paths"):
            repos.add("sumipan/diary")
    return repos


def _allow_paths_union(body: str) -> set[str]:
    paths: set[str] = set()
    for metadata in parse_issue_metadata_blocks(body):
        raw = metadata.get("allow_paths") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        paths.update(p.strip() for p in raw if isinstance(p, str) and p.strip())
    return paths


def _sub_plan_dependencies(body: str) -> dict[int, set[int]]:
    """``{row: {depended rows}}`` from the sub-plan table's depends-on column (#4745)."""
    milestone = get_section(body, get_config().sections["milestone"])
    if not milestone:
        return {}
    plan_match = re.search(
        rf"###\s+{re.escape(get_config().sections['sub_plan'])}\s*\n(.*?)(?=^###|\Z)",
        milestone,
        re.MULTILINE | re.DOTALL,
    )
    if not plan_match:
        return {}
    rows = parse_table_rows(plan_match.group(1))
    if len(rows) <= 1:
        return {}
    dep_column = get_config().language.sub_plan_columns[4]
    dep_i = next((i for i, cell in enumerate(rows[0]) if dep_column in cell), None)
    if dep_i is None:
        return {}
    deps: dict[int, set[int]] = {}
    for row in rows[1:]:
        if len(row) <= dep_i or not row[0].strip().isdigit():
            continue
        deps[int(row[0].strip())] = {int(n) for n in re.findall(r"\d+", row[dep_i])}
    return deps


def _normalize_stem(stem: str) -> str:
    """Fold case and treat ``-`` / ``_`` as one separator for stem matching (#4745)."""
    return stem.lower().replace("-", "_")


def _extract_ac_items(section: str) -> list[str]:
    ac = get_config().sections["acceptance_criteria"]
    ac_match = re.search(
        rf"\*\*{re.escape(ac)}\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
        section,
        re.DOTALL,
    )
    if not ac_match:
        return []
    ac_text = ac_match.group(1)
    return re.findall(r"^\s*-\s+\[[ xX]\]\s+(.*)$", ac_text, re.MULTILINE)


def _extract_file_refs_from_text(text: str) -> set[str]:
    refs: set[str] = set()
    for match in _FILE_REF_RE.finditer(text):
        path = match.group(1) or match.group(2)
        if path and not path.startswith("http"):
            refs.add(_normalize_path(path))
    return refs


class B1MilestoneSubdesignRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if "scope:milestone" not in labels:
            return []

        violations: list[Violation] = []
        violations.extend(self._check_sub_count(body))
        sub_blocks = extract_sub_blocks(body)
        for sub_num, block in sub_blocks:
            violations.extend(self._check_required_subsections(sub_num, block))
            violations.extend(self._check_table_schema(sub_num, block))
            violations.extend(self._check_repo_column(body, sub_num, block))
            violations.extend(self._check_change_paths_readable(body, sub_num, block))
            violations.extend(self._check_sub_ac(sub_num, block))
        violations.extend(self._check_file_union(body, sub_blocks))
        violations.extend(self._check_impact_scope(body, sub_blocks))
        violations.extend(self._check_deletion_reference_orphan(body, sub_blocks))
        violations.extend(self._check_behavior_test_in_sibling(
            sub_blocks, _sub_plan_dependencies(body)
        ))
        for sub_num, block in sub_blocks:
            violations.extend(self._check_sub_ac_contradiction(sub_num, block))
        return violations

    def _check_sub_count(self, body: str) -> list[Violation]:
        cfg = get_config()
        sections = cfg.sections
        sub_prefix = cfg.language.sub_header_prefix
        plan_count = _count_sub_plan_rows(body)
        if plan_count is None:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_plan_missing",
                severity="fail",
                message=(
                    f"no ### {sections['sub_plan']} table under "
                    f"## {sections['milestone']}"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]
        sub_headers = _SUB_HEADER_RE.findall(
            get_section(body, sections["design"]) or ""
        )
        header_count = len(sub_headers)
        if plan_count != header_count:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_count_mismatch",
                severity="fail",
                message=(
                    f"{sections['sub_plan']} table row count ({plan_count}) does not match"
                    f" the #### {sub_prefix}N header count ({header_count})"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=(
                    f"For every `{sections['sub_plan']}` row N, add a "
                    f"`#### {sub_prefix}N: <title>` block "
                    f"under `## {sections['design']}` with the required subsections "
                    + " / ".join(
                        f"**{name}**" for name in cfg.sub_design_subsections
                    )
                    + ". Do not use the spaced `Sub N` header form "
                    "(milestone_consistency.sub_header_english rejects them)."
                ),
            )]
        return []

    def _check_required_subsections(self, sub_num: int, block: str) -> list[Violation]:
        violations: list[Violation] = []
        for name in get_config().sub_design_subsections:
            if not re.search(rf"\*\*{re.escape(name)}\*\*", block):
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.subsection_missing",
                    severity="fail",
                    message=f"Sub {sub_num}: required subsection **{name}** is missing",
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations

    def _check_table_schema(self, sub_num: int, block: str) -> list[Violation]:
        changed = get_config().sections["changed_files"]
        table_match = re.search(
            rf"\*\*{re.escape(changed)}\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
            block,
            re.DOTALL,
        )
        if not table_match:
            return []
        rows = parse_table_rows(table_match.group(1))
        if not rows:
            return [Violation(
                rule_id="b1_milestone_subdesign.table_schema",
                severity="fail",
                message=f"Sub {sub_num}: the {changed} table is empty",
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=None,
            )]
        header = rows[0]
        expected = get_config().language.change_table_columns
        if len(header) != 4:
            return [Violation(
                rule_id="b1_milestone_subdesign.table_schema",
                severity="fail",
                message=(
                    f"Sub {sub_num}: the {changed} table does not have the 4-column schema"
                    f" ({' / '.join(expected)}; got {len(header)} columns)"
                ),
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=None,
            )]
        for col, exp in zip(header, expected):
            if exp not in col:
                return [Violation(
                    rule_id="b1_milestone_subdesign.table_schema",
                    severity="fail",
                    message=(
                        f"Sub {sub_num}: invalid {changed} table column names"
                        f" (expected: {' / '.join(expected)})"
                    ),
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=None,
                )]
        return []

    def _check_repo_column(self, body: str, sub_num: int, block: str) -> list[Violation]:
        allowed = _allowed_repos(body)
        if not allowed:
            return []
        violations: list[Violation] = []
        for repo, path, _ in _extract_paths_from_change_table(block):
            if repo not in allowed:
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.repo_mismatch",
                    severity="fail",
                    message=(
                        f"Sub {sub_num}: repository column `{repo}` does not match"
                        f" the target_repo / diary_allow_paths of any metadata block"
                        f" ({path})"
                    ),
                    location=_sub_location(sub_num),
                    auto_fixable=True,
                    fix_hint=(
                        "add a separate ```yaml metadata block for this repo at the top of the"
                        " body (keep the existing blocks unchanged):\n"
                        f"target_repo: {repo}\nbase_branch: main\nallow_paths:\n  - {path}"
                    ),
                ))
        return violations

    def _check_change_paths_readable(
        self, body: str, sub_num: int, block: str
    ) -> list[Violation]:
        """R3 parity for SUB1: the child allow_paths are derived from this table.

        SUB1 (issuesmith.milestone) calls change_paths_for_repo(block, row_repo)
        and fails the row when it returns []. This gate runs the same call at B1
        so an unreadable table is caught before any child Issue exists.
        """
        table_repos = sorted(
            {repo for repo, _, _ in extract_change_table_rows(block) if repo}
        )
        if table_repos:
            repos = table_repos
        else:
            allowed = sorted(_allowed_repos(body))
            repos = allowed[:1] or [""]
        violations: list[Violation] = []
        for repo in repos:
            if change_paths_for_repo(block, repo or None):
                continue
            cfg = get_config()
            changed = cfg.sections["changed_files"]
            repository_col, file_path_col = cfg.language.change_table_columns[:2]
            header = "| " + " | ".join(cfg.language.change_table_columns) + " |"
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.change_paths_unreadable",
                severity="fail",
                message=(
                    f"Sub {sub_num}: no file paths for `{repo or '(no repo)'}` can be"
                    f" extracted from the {changed} table (SUB1 builds the child"
                    " allow_paths with the same extraction, so no child can be created)"
                ),
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=(
                    f"put the 4-column table {header} right after **{changed}**:,"
                    f" with owner/repo in the {repository_col} column and a path containing"
                    f" / in the {file_path_col} column"
                ),
            ))
        return violations

    def _check_sub_ac(self, sub_num: int, block: str) -> list[Violation]:
        violations: list[Violation] = []
        ac = get_config().sections["acceptance_criteria"]
        items = _extract_ac_items(block)
        if len(items) < 3:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.ac_count",
                severity="fail",
                message=f"Sub {sub_num}: {ac} has {len(items)} item(s) (3 or more required)",
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=None,
            ))
        vague_words = get_config().language.vague_ac_words
        for item in items:
            for word in vague_words:
                if word in item:
                    violations.append(Violation(
                        rule_id="b1_milestone_subdesign.ac_vague_word",
                        severity="fail",
                        message=f"Sub {sub_num}: {ac} contains the vague word `{word}`",
                        location=_sub_location(sub_num),
                        auto_fixable=False,
                        fix_hint=None,
                    ))
                    break
        return violations

    def _check_file_union(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        sections = get_config().sections
        changed = sections["changed_files"]
        design = sections["design"]
        parent_paths = _extract_parent_change_paths(body)
        if not parent_paths:
            # Milestone parents may have no changed-files section; the blocks'
            # allow_paths are then the parent's path set (#4076).
            parent_paths = _allow_paths_union(body)
        sub_paths: list[str] = []
        for _, block in sub_blocks:
            for _, path, _ in _extract_paths_from_change_table(block):
                sub_paths.append(path)
        sub_set = set(sub_paths)
        violations: list[Violation] = []
        missing_in_subs = parent_paths - sub_set
        missing_in_parent = sub_set - parent_paths
        if missing_in_subs:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_missing_in_subs",
                severity="fail",
                message=(
                    f"paths in the parent {changed} but in no sub: "
                    + ", ".join(sorted(missing_in_subs))
                ),
                location=f"## {changed}",
                auto_fixable=False,
                fix_hint=None,
            ))
        if missing_in_parent:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_missing_in_parent",
                severity="fail",
                message=(
                    f"paths in a sub {changed} but not in the parent: "
                    + ", ".join(sorted(missing_in_parent))
                ),
                location=f"## {changed}",
                auto_fixable=False,
                fix_hint=None,
            ))
        duplicates = {p for p in sub_paths if sub_paths.count(p) > 1}
        if duplicates:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_duplicate",
                severity="fail",
                message=f"{changed} duplicated across subs: " + ", ".join(sorted(duplicates)),
                location=f"## {design}",
                auto_fixable=False,
                fix_hint=None,
            ))
        return violations

    def _check_impact_scope(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        sections = get_config().sections
        impact_name = sections["impact_survey"]
        changed = sections["changed_files"]
        impact = get_section(body, impact_name)
        if not impact:
            return []
        allowed_paths = _extract_parent_change_paths(body)
        for _, block in sub_blocks:
            for _, path, _ in _extract_paths_from_change_table(block):
                allowed_paths.add(path)
        refs = _extract_file_refs_from_text(impact)
        violations: list[Violation] = []
        for ref in refs:
            if ref not in allowed_paths:
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.impact_scope_pollution",
                    severity="fail",
                    message=(
                        f"{impact_name} file reference `{ref}` is not in"
                        f" the parent or sub {changed}"
                    ),
                    location=f"## {impact_name}",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations

    def _check_deletion_reference_orphan(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        """B1 twin of ``scope_coupling.deletion_reference_uncovered`` (#4518).

        A deleted file whose referrers no sub change table owns makes the deleting
        sub fail P1 with nobody allowed to fix the referrers.
        """
        from issuesmith.gate_rules import scope_coupling

        keywords = scope_coupling._delete_move_keywords()
        if not any(
            any(word in change_type.lower() for word in keywords)
            for _, block in sub_blocks
            for _, _, change_type in _extract_paths_from_change_table(block)
        ):
            return []
        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []
        if scope_coupling.resolve_scope_root(metadata, get_config()) is None:
            return []
        refs = scope_coupling.deletion_references_for_body(body)
        design = get_config().sections["design"]
        return [
            Violation(
                rule_id="b1_milestone_subdesign.deletion_reference_orphan",
                severity="fail",
                message=(
                    f"files referencing deleted `{path}` are in no sub change table: "
                    + ", ".join(files)
                ),
                location=f"## {design}",
                auto_fixable=False,
                fix_hint=(
                    "add each referrer to some sub's change table, or stage the deletion"
                    " (keep an alias first, delete it in the last sub)"
                ),
            )
            for path, files in refs.items()
        ]

    def _check_behavior_test_in_sibling(
        self,
        sub_blocks: list[tuple[int, str]],
        dependencies: dict[int, set[int]],
    ) -> list[Violation]:
        """Reject an impl file whose mirror test sits in a sibling sub (#4518).

        A test sub that declares the impl sub in its depends-on cell is allowed (#4745).
        """
        owned = [
            (sub_num, repo, path)
            for sub_num, block in sub_blocks
            for repo, path, _ in _extract_paths_from_change_table(block)
        ]
        tests = [row for row in owned if row[2].startswith("tests/")]
        violations: list[Violation] = []
        for sub_num, repo, path in owned:
            if path.startswith("tests/"):
                continue
            stem = _normalize_stem(Path(path).stem)
            stem_re = re.compile(rf"(?<![a-z0-9]){re.escape(stem)}(?![a-z0-9])")
            for test_sub, test_repo, test_path in tests:
                if test_sub == sub_num or test_repo != repo:
                    continue
                if not stem_re.search(_normalize_stem(Path(test_path).stem)):
                    continue
                if sub_num in dependencies.get(test_sub, set()):
                    continue
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.behavior_test_in_sibling",
                    severity="fail",
                    message=(
                        f"Sub {sub_num}: `{path}` is changed here but its test"
                        f" `{test_path}` is in Sub {test_sub}"
                    ),
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=(
                        "move the test into the same sub's change table as the impl,"
                        f" merge the two subs, or make Sub {test_sub} depend on Sub {sub_num}"
                    ),
                ))
        return violations

    def _check_sub_ac_contradiction(self, sub_num: int, block: str) -> list[Violation]:
        """Reject a sub that says it fails tests alone yet requires a full pass (#4518)."""
        ac = get_config().sections["acceptance_criteria"]
        design_text = re.split(rf"\*\*{re.escape(ac)}\*\*", block, maxsplit=1)[0]
        if not any(pattern.search(design_text) for pattern in _STANDALONE_FAIL_RES):
            return []
        full_suite = any(_FULL_SUITE_AC_RE.search(item) for item in _extract_ac_items(block))
        if not full_suite:
            return []
        return [Violation(
            rule_id="b1_milestone_subdesign.sub_ac_contradiction",
            severity="fail",
            message=(
                f"Sub {sub_num}: the design says this sub fails the tests on its own,"
                f" but its {ac} require the whole test suite to pass"
            ),
            location=_sub_location(sub_num),
            auto_fixable=False,
            fix_hint=(
                f"drop the whole-suite requirement from {ac}, or remove the"
                " 'another sub handles it' split (move the referrers into this sub,"
                " or stage the change)"
            ),
        )]


GATE_REGISTRY["b1_milestone_subdesign"] = B1MilestoneSubdesignRules
