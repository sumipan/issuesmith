from __future__ import annotations

import re

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config


def has_acceptance_criteria_section(body: str) -> bool:
    """AC セクションが存在するかを返す。"""
    heading = get_config().sections["acceptance_criteria"]
    return bool(
        re.search(rf"^##\s+{re.escape(heading)}", body, re.MULTILINE)
    )


def get_unchecked_count(body: str) -> int:
    """AC セクション内の未チェック checkbox 数を返す。"""
    heading = get_config().sections["acceptance_criteria"]
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return 0
    section = match.group(1)
    return len(re.findall(r"^\s*-\s+\[ \]", section, re.MULTILINE))


class M2Rules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        ac = get_config().sections["acceptance_criteria"]
        if not has_acceptance_criteria_section(body):
            return [Violation(
                rule_id="m2.ac_section_missing",
                severity="warn",
                message=f"{ac}セクションが見つかりません",
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
            message=f"未チェックの{ac}が {unchecked} 件あります",
            location=None,
            auto_fixable=False,
            fix_hint=None,
        )]


GATE_REGISTRY["m2"] = M2Rules
