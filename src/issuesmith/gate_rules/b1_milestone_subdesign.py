from __future__ import annotations

import re

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import (  # noqa: F401 — re-exported for legacy importers
    SUB_HEADER_RE,
    _normalize_path,
    change_paths_for_repo,
    extract_change_table_rows,
    get_section,
    parse_table_rows,
)

_VAGUE_AC_WORDS = ("正しく動作", "適切に", "問題なく", "きちんと", "ちゃんと", "必要に応じて")
_SUB_HEADER_RE = SUB_HEADER_RE
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
_FILE_REF_RE = re.compile(r"`([^`]+\.[a-zA-Z0-9]+)`|(?:^|[\s(/])([\w./-]+\.[a-zA-Z0-9]+)")


def extract_sub_blocks(body: str) -> list[tuple[int, str]]:
    design = get_section(body, get_config().sections["design"])
    if not design:
        return []
    headers = list(_SUB_HEADER_RE.finditer(design))
    blocks: list[tuple[int, str]] = []
    for idx, match in enumerate(headers):
        sub_num = int(match.group(1))
        start = match.start()
        end = headers[idx + 1].start() if idx + 1 < len(headers) else len(design)
        blocks.append((sub_num, design[start:end]))
    return blocks


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
    try:
        metadata = parse_issue_metadata(body)
    except (ValueError, Exception):
        return set()
    repos: set[str] = set()
    target_repo = metadata.get("target_repo")
    if isinstance(target_repo, str) and target_repo.strip():
        repos.add(target_repo.strip())
    diary_paths = metadata.get("diary_allow_paths", [])
    if isinstance(diary_paths, str):
        diary_paths = [diary_paths]
    if diary_paths:
        repos.add("sumipan/diary")
    return repos


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
        return violations

    def _check_sub_count(self, body: str) -> list[Violation]:
        sections = get_config().sections
        plan_count = _count_sub_plan_rows(body)
        if plan_count is None:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_plan_missing",
                severity="fail",
                message=(
                    f"## {sections['milestone']} 内に "
                    f"### {sections['sub_plan']} テーブルが存在しません"
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
                    f"{sections['sub_plan']}テーブルの行数 ({plan_count}) と"
                    f" #### サブN ヘッダ数 ({header_count}) が一致しません"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=(
                    f"For every `{sections['sub_plan']}` row N, add `#### Sub N: <title>` "
                    f"under `## {sections['design']}` with the required subsections "
                    + " / ".join(
                        f"**{name}**" for name in get_config().sub_design_subsections
                    )
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
                    message=f"サブ{sub_num} に必須サブセクション **{name}** がありません",
                    location=f"#### サブ{sub_num}",
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
                message=f"サブ{sub_num} の{changed}テーブルが空です",
                location=f"#### サブ{sub_num}",
                auto_fixable=False,
                fix_hint=None,
            )]
        header = rows[0]
        if len(header) != 4:
            return [Violation(
                rule_id="b1_milestone_subdesign.table_schema",
                severity="fail",
                message=(
                    f"サブ{sub_num} の{changed}テーブルが 4 列スキーマ"
                    f"（リポジトリ / ファイルパス / 変更種別 / 変更内容）ではありません"
                    f"（{len(header)} 列）"
                ),
                location=f"#### サブ{sub_num}",
                auto_fixable=False,
                fix_hint=None,
            )]
        expected = ("リポジトリ", "ファイルパス", "変更種別", "変更内容")
        for col, exp in zip(header, expected):
            if exp not in col:
                return [Violation(
                    rule_id="b1_milestone_subdesign.table_schema",
                    severity="fail",
                    message=(
                        f"サブ{sub_num} の{changed}テーブル列名が不正です"
                        f"（期待: {' / '.join(expected)}）"
                    ),
                    location=f"#### サブ{sub_num}",
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
                        f"サブ{sub_num} のリポジトリ列 `{repo}` が"
                        f" target_repo / diary_allow_paths と一致しません（{path}）"
                    ),
                    location=f"#### サブ{sub_num}",
                    auto_fixable=True,
                    fix_hint=f"target_repo: {repo}",
                ))
        return violations

    def _check_change_paths_readable(
        self, body: str, sub_num: int, block: str
    ) -> list[Violation]:
        """R3 parity for SUB1: the child allow_paths are derived from this table.

        SUB1 (steps/sub1_create.py) calls change_paths_for_repo(block, row_repo)
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
            changed = get_config().sections["changed_files"]
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.change_paths_unreadable",
                severity="fail",
                message=(
                    f"サブ{sub_num} の{changed}表から `{repo or '(repo なし)'}` の"
                    " ファイルパスを抽出できません（SUB1 は同じ抽出で子の allow_paths を作るため、"
                    "このままでは子を作れません）"
                ),
                location=f"#### サブ{sub_num}",
                auto_fixable=False,
                fix_hint=(
                    f"**{changed}**: の直後に | リポジトリ | ファイルパス | 変更種別 | 変更内容 |"
                    " の 4 列表を置き、リポジトリ列に owner/repo、ファイルパス列に / を含むパスを書く"
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
                message=f"サブ{sub_num} の{ac}が {len(items)} 件（3 件以上必要）",
                location=f"#### サブ{sub_num}",
                auto_fixable=False,
                fix_hint=None,
            ))
        for item in items:
            for word in _VAGUE_AC_WORDS:
                if word in item:
                    violations.append(Violation(
                        rule_id="b1_milestone_subdesign.ac_vague_word",
                        severity="fail",
                        message=f"サブ{sub_num} の{ac}に曖昧語 `{word}` が含まれます",
                        location=f"#### サブ{sub_num}",
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
                    f"親の{changed}にあってサブにないパス: "
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
                    f"サブの{changed}にあって親にないパス: "
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
                message=f"サブ間で重複する{changed}: " + ", ".join(sorted(duplicates)),
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
                        f"{impact_name}のファイル参照 `{ref}` が"
                        f" 親またはサブの{changed}に含まれません"
                    ),
                    location=f"## {impact_name}",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations


GATE_REGISTRY["b1_milestone_subdesign"] = B1MilestoneSubdesignRules
