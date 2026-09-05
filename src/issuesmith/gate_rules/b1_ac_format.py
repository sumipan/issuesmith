from __future__ import annotations

import re

import yaml
from ghdag.workflow.gates import GATE_REGISTRY, Violation

_ALLOWED_KEYS = frozenset({"paths_must_exist", "paths_must_not_exist", "references_must_resolve"})


def get_ac_section(body: str) -> str | None:
    match = re.search(
        r"^##\s+受け入れ条件\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def extract_yaml_block(section: str) -> str | None:
    match = re.search(r"^```yaml\n(.*?)\n```", section, re.DOTALL | re.MULTILINE)
    return match.group(1) if match else None


def _extract_new_file_paths_from_design(body: str) -> list[str]:
    """## 設計 内の変更対象ファイルテーブルから「新規」行のパスを抽出する。"""
    match = re.search(
        r"^##\s+設計\s*\n(.*?)(?=^##[^#]|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    design_section = match.group(1)
    paths: list[str] = []
    for line in design_section.splitlines():
        if "新規" in line:
            path_match = re.search(r"`([^`]+\.[a-z]+)`", line)
            if path_match:
                paths.append(path_match.group(1))
    return paths


class B1AcFormatRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        # migration Issue も YAML 契約（paths_must_exist 等）の形式検証対象
        if "scope:milestone" not in labels and "scope:migration" not in labels:
            return []

        section = get_ac_section(body)
        if section is None:
            return [Violation(
                rule_id="b1_ac_format.section_missing",
                severity="fail",
                message="## 受け入れ条件 セクションが存在しません",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        yaml_text = extract_yaml_block(section)
        if yaml_text is None:
            new_paths = _extract_new_file_paths_from_design(body)
            if new_paths:
                hint_lines = ["```yaml", "paths_must_exist:"]
                hint_lines.extend(f"  - {p}" for p in new_paths)
                hint_lines.append("```")
                fix_hint = "\n".join(hint_lines)
            else:
                fix_hint = "変更対象ファイルテーブルから paths_must_exist を自動派生"
            return [Violation(
                rule_id="b1_ac_format.yaml_block_missing",
                severity="fail",
                message="## 受け入れ条件 セクション内に ```yaml ブロックが存在しません",
                location=None,
                auto_fixable=True,
                fix_hint=fix_hint,
            )]

        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return [Violation(
                rule_id="b1_ac_format.yaml_invalid",
                severity="fail",
                message="YAML ブロックのパースに失敗しました",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        if not isinstance(data, dict):
            return [Violation(
                rule_id="b1_ac_format.yaml_invalid",
                severity="fail",
                message="YAML ブロックはマッピングである必要があります",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        unknown_keys = set(data.keys()) - _ALLOWED_KEYS
        if unknown_keys:
            return [Violation(
                rule_id="b1_ac_format.yaml_invalid",
                severity="fail",
                message=f"許可されていないキーが含まれています: {sorted(unknown_keys)}",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        return []


GATE_REGISTRY["b1_ac_format"] = B1AcFormatRules
