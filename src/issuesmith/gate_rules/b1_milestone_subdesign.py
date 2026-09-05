from __future__ import annotations

import re

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.context_hook import parse_issue_metadata

_REQUIRED_SUBSECTIONS = ("スコープ", "設計方針", "変更対象ファイル", "受け入れ条件")
_VAGUE_AC_WORDS = ("正しく動作", "適切に", "問題なく", "きちんと", "ちゃんと", "必要に応じて")
_SUB_HEADER_RE = re.compile(r"^####\s+サブ(\d+):", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
_FILE_REF_RE = re.compile(r"`([^`]+\.[a-zA-Z0-9]+)`|(?:^|[\s(/])([\w./-]+\.[a-zA-Z0-9]+)")


_SECTION_END = r"(?=^##(?!#)|\Z)"


def get_section(body: str, heading: str) -> str | None:
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?){_SECTION_END}",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def parse_table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not _TABLE_ROW_RE.match(stripped):
            continue
        if _TABLE_SEP_RE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


def extract_sub_blocks(body: str) -> list[tuple[int, str]]:
    design = get_section(body, "設計")
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
    milestone = get_section(body, "マイルストーン")
    if not milestone:
        return None
    plan_match = re.search(
        r"###\s+サブイシュー分割計画\s*\n(.*?)(?=^###|\Z)",
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


def _normalize_path(path: str) -> str:
    path = path.strip().strip("`").strip()
    return path


def _extract_paths_from_table_section(section: str) -> list[tuple[str, str, str]]:
    """Parse change-target table rows from a section (with or without bold header)."""
    table_match = re.search(
        r"\*\*変更対象ファイル\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
        section,
        re.DOTALL,
    )
    target = table_match.group(1) if table_match else section
    rows = parse_table_rows(target)
    if len(rows) <= 1:
        return []
    header = [c.lower() for c in rows[0]]
    try:
        repo_idx = next(i for i, c in enumerate(header) if "リポジトリ" in c)
        path_idx = next(i for i, c in enumerate(header) if "ファイルパス" in c or "パス" in c)
        type_idx = next(i for i, c in enumerate(header) if "変更種別" in c or "種別" in c)
    except StopIteration:
        if len(rows[0]) >= 2 and "ファイル" not in rows[0][0]:
            result: list[tuple[str, str, str]] = []
            for row in rows:
                if len(row) >= 2 and "ファイル" not in row[0]:
                    path = _normalize_path(row[0] if "`" in row[0] else row[1])
                    change_type = row[1] if "`" in row[0] else (row[2] if len(row) > 2 else "")
                    if path and "/" in path:
                        result.append(("", path, change_type))
            return result
        return []
    result: list[tuple[str, str, str]] = []
    for row in rows[1:]:
        if len(row) <= max(repo_idx, path_idx, type_idx):
            continue
        repo = row[repo_idx].strip().strip("`")
        path = _normalize_path(row[path_idx])
        change_type = row[type_idx].strip()
        if path:
            result.append((repo, path, change_type))
    return result


def _extract_paths_from_change_table(section: str) -> list[tuple[str, str, str]]:
    return _extract_paths_from_table_section(section)


def _extract_parent_change_paths(body: str) -> set[str]:
    section = get_section(body, "変更対象ファイル")
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
    ac_match = re.search(
        r"\*\*受け入れ条件\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
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
            violations.extend(self._check_sub_ac(sub_num, block))
        violations.extend(self._check_file_union(body, sub_blocks))
        violations.extend(self._check_impact_scope(body, sub_blocks))
        return violations

    def _check_sub_count(self, body: str) -> list[Violation]:
        plan_count = _count_sub_plan_rows(body)
        if plan_count is None:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_plan_missing",
                severity="fail",
                message="## マイルストーン 内に ### サブイシュー分割計画 テーブルが存在しません",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]
        sub_headers = _SUB_HEADER_RE.findall(get_section(body, "設計") or "")
        header_count = len(sub_headers)
        if plan_count != header_count:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_count_mismatch",
                severity="fail",
                message=(
                    f"サブイシュー分割計画テーブルの行数 ({plan_count}) と"
                    f" #### サブN ヘッダ数 ({header_count}) が一致しません"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]
        return []

    def _check_required_subsections(self, sub_num: int, block: str) -> list[Violation]:
        violations: list[Violation] = []
        for name in _REQUIRED_SUBSECTIONS:
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
        table_match = re.search(
            r"\*\*変更対象ファイル\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
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
                message=f"サブ{sub_num} の変更対象ファイルテーブルが空です",
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
                    f"サブ{sub_num} の変更対象ファイルテーブルが 4 列スキーマ"
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
                        f"サブ{sub_num} の変更対象ファイルテーブル列名が不正です"
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

    def _check_sub_ac(self, sub_num: int, block: str) -> list[Violation]:
        violations: list[Violation] = []
        items = _extract_ac_items(block)
        if len(items) < 3:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.ac_count",
                severity="fail",
                message=f"サブ{sub_num} の受け入れ条件が {len(items)} 件（3 件以上必要）",
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
                        message=f"サブ{sub_num} の受け入れ条件に曖昧語 `{word}` が含まれます",
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
                    "親の変更対象ファイルにあってサブにないパス: "
                    + ", ".join(sorted(missing_in_subs))
                ),
                location="## 変更対象ファイル",
                auto_fixable=False,
                fix_hint=None,
            ))
        if missing_in_parent:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_missing_in_parent",
                severity="fail",
                message=(
                    "サブの変更対象ファイルにあって親にないパス: "
                    + ", ".join(sorted(missing_in_parent))
                ),
                location="## 変更対象ファイル",
                auto_fixable=False,
                fix_hint=None,
            ))
        duplicates = {p for p in sub_paths if sub_paths.count(p) > 1}
        if duplicates:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_duplicate",
                severity="fail",
                message="サブ間で重複する変更対象ファイル: " + ", ".join(sorted(duplicates)),
                location="## 設計",
                auto_fixable=False,
                fix_hint=None,
            ))
        return violations

    def _check_impact_scope(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        impact = get_section(body, "影響範囲調査")
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
                        f"影響範囲調査のファイル参照 `{ref}` が"
                        " 親またはサブの変更対象ファイルに含まれません"
                    ),
                    location="## 影響範囲調査",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations


GATE_REGISTRY["b1_milestone_subdesign"] = B1MilestoneSubdesignRules
