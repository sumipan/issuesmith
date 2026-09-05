from __future__ import annotations

import re

from ghdag.workflow.gates import GATE_REGISTRY, Violation


def has_acceptance_criteria_section(body: str) -> bool:
    """## 受け入れ条件 セクションが存在するかを返す。"""
    return bool(re.search(r"^##\s+受け入れ条件", body, re.MULTILINE))


def get_unchecked_count(body: str) -> int:
    """## 受け入れ条件 セクション内の未チェック checkbox 数を返す。"""
    match = re.search(
        r"^##\s+受け入れ条件\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return 0
    section = match.group(1)
    return len(re.findall(r"^\s*-\s+\[ \]", section, re.MULTILINE))


class M2Rules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if not has_acceptance_criteria_section(body):
            return [Violation(
                rule_id="m2.ac_section_missing",
                severity="warn",
                message="受け入れ条件セクションが見つかりません",
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        unchecked = get_unchecked_count(body)
        if unchecked == 0:
            return []

        return [Violation(
            rule_id="m2.unchecked_ac",
            severity="fail",
            message=f"未チェックの受け入れ条件が {unchecked} 件あります",
            location=None,
            auto_fixable=False,
            fix_hint=None,
        )]


GATE_REGISTRY["m2"] = M2Rules
