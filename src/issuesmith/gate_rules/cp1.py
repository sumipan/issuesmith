from __future__ import annotations

import re

import yaml
from ghdag.workflow.gates import GATE_REGISTRY, Violation
from ghdag.workflow.gates.common import strip_code_regions

from issuesmith.context_hook import parse_issue_metadata, validate_issue_metadata
from issuesmith.gate_rules.b1_ac_format import extract_yaml_block, get_ac_section
from issuesmith.gate_rules.b1_milestone_subdesign import (
    _extract_paths_from_change_table,
    extract_sub_blocks,
)

_YAML_CONTRACT_FIXES: dict[str, tuple[bool, str]] = {
    "missing_required": (
        True,
        "冒頭 YAML ブロックに `target_repo: sumipan/nexus`（nexus 本体）または"
        " `target_repo: sumipan/<repo>` を追加してください",
    ),
    "annotation_in_path": (
        True,
        "allow_paths から括弧付き注記（`(...)` 形式）を除去し、ファイルパスのみを記載してください",
    ),
    "invalid_path_format": (
        True,
        "allow_paths から `/var/tmp/` で始まるパスを除去してください",
    ),
}
_YAML_CONTRACT_DEFAULT_FIX = (False, "target_repo を対応リポジトリに修正してください")
_META_AC_PATTERN = re.compile(
    r"(PR\s*#\d+|子\s*Issue\s*起票|本\s*Issue\s*を\s*close|サブイシューすべての実装)"
)
_OPTIONAL_AC_PATTERN = re.compile(r"^（オプション）")
_VERSION_ASSIGN_LINE = re.compile(r"^\s*version\s*=")


def check_version_line_in_diff(diff: str) -> list[Violation]:
    """LLM が提出した unified diff に pyproject.toml の `version =` 変更があれば拒否する (#2766).

    バージョンバンプは `scripts/issuesmith-version-bump.py` が決定論的に行うため、
    実装 LLM が version 行を書き換える経路を構造的に封じる。
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
                        "pyproject.toml の version = 行が LLM diff に含まれています。"
                        "バージョンバンプは issuesmith-version-bump.py が決定論的に行います"
                    ),
                    location="pyproject.toml",
                    auto_fixable=False,
                    fix_hint=(
                        "diff から version = 行の変更を取り除いてください。"
                        "バンプは publish ステップで自動適用されます"
                    ),
                )
            ]
    return []


def _yaml_contract_fix(code: str) -> tuple[bool, str]:
    return _YAML_CONTRACT_FIXES.get(code, _YAML_CONTRACT_DEFAULT_FIX)


def _extract_sub_ac_section(block: str) -> str | None:
    match = re.search(
        r"\*\*受け入れ条件\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
        block,
        re.DOTALL,
    )
    return match.group(1) if match else None


def _extract_ac_checkbox_items(section: str) -> list[str]:
    return re.findall(r"^\s*-\s+\[[ xX]\]\s+(.*)$", section, re.MULTILINE)


def _keyword_tokens(text: str) -> list[str]:
    return [
        t
        for t in re.split(r"[\s・、。：:（）/]", text.strip())
        if len(t) >= 3
    ]


class Cp1Rules:
    FAIL_PATTERNS: list[tuple[str, str, str, str]] = [
        (r"TODO:", "cp1.forbidden_word.todo", "TODO: が残存", "具体的な記述に置換してください"),
        (r"TBD", "cp1.forbidden_word.tbd", "TBD が残存", "具体的な記述に置換してください"),
        (r"要確認", "cp1.forbidden_word.youkakunin", "「要確認」が残存", "具体的な記述に置換してください"),
        (r"未定(?!義)", "cp1.forbidden_word.mitei", "「未定」が残存", "具体的な記述に置換してください"),
        (r"検討中", "cp1.forbidden_word.kentouchuu", "「検討中」が残存", "具体的な記述に置換してください"),
        (r"ユーザーに確認", "cp1.forbidden_word.user_confirm", "「ユーザーに確認」が残存", "具体的な記述に置換してください"),
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
                message="cp1_must_fail: true が設定されている",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            ))

        # YAML 契約検証。冒頭ブロックの欠落・パース不能は missing_block として fail。
        # スキップすると「YAML の無い draft-done」が素通りし、develop 入口の P0 で
        # 初めて停止する（#2539/#2541）。
        try:
            metadata = parse_issue_metadata(body)
        except (ValueError, yaml.YAMLError) as exc:
            violations.append(Violation(
                rule_id="cp1.yaml_contract.missing_block",
                severity="fail",
                message=f"冒頭の yaml メタデータブロックが欠落またはパース不能です: {exc}",
                location=None,
                auto_fixable=True,
                fix_hint=(
                    "Issue body の先頭に以下の形式の yaml ブロックを新設する"
                    "（allow_paths は「変更対象ファイル」テーブルから導出、"
                    "外部リポジトリ対象なら target_repo を明記）:\n"
                    "```yaml\n"
                    "base_branch: main\n"
                    "allow_paths:\n"
                    "  - \"<変更対象のパスパターン>\"\n"
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

        if "scope:milestone" in labels:
            violations.append(Violation(
                rule_id="cp1.intentional_hold",
                severity="fail",
                message="scope:milestone ラベルが付与されている（CP1 常時 FAIL）",
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
                        message=f"サブ{sub_num}: {message}",
                        location=f"#### サブ{sub_num}",
                        auto_fixable=True,
                        fix_hint=fix_hint,
                    ))
        return violations

    def _check_milestone_sub_ac_yaml(self, body: str) -> list[Violation]:
        violations: list[Violation] = []
        for sub_num, block in extract_sub_blocks(body):
            ac_section = _extract_sub_ac_section(block)
            if ac_section is None:
                violations.append(Violation(
                    rule_id="cp1.milestone.sub_ac_yaml_missing",
                    severity="fail",
                    message=f"サブ{sub_num} の受け入れ条件セクションが存在しません",
                    location=f"#### サブ{sub_num}",
                    auto_fixable=False,
                    fix_hint=None,
                ))
                continue
            if extract_yaml_block(ac_section) is None:
                violations.append(Violation(
                    rule_id="cp1.milestone.sub_ac_yaml_missing",
                    severity="fail",
                    message=f"サブ{sub_num} の受け入れ条件に ```yaml ブロックがありません",
                    location=f"#### サブ{sub_num}",
                    auto_fixable=True,
                    fix_hint="受け入れ条件先頭に paths_must_exist YAML ブロックを追加してください",
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
                    message=f"親 AC がいずれのサブ AC でもカバーされていません: {item[:80]}",
                    location="## 受け入れ条件",
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
                if "新規" in change_type or "修正" in change_type:
                    mapped_paths.add(path)

        violations: list[Violation] = []
        for path in paths_must_exist:
            if not isinstance(path, str):
                continue
            normalized = path.strip().strip("`")
            if normalized not in mapped_paths:
                violations.append(Violation(
                    rule_id="cp1.milestone.paths_must_exist_unmapped",
                    severity="fail",
                    message=(
                        f"paths_must_exist の `{normalized}` が"
                        " いずれのサブの「新規」または「修正」行にも記載されていません"
                    ),
                    location="## 受け入れ条件",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations


GATE_REGISTRY["cp1"] = Cp1Rules
