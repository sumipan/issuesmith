"""milestone_consistency — 分割計画パターンと scope:milestone ラベルの矛盾検知。"""
from __future__ import annotations

import re

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.gate_rules.b1_milestone_subdesign import get_section

_SUB_HEADER_RE = re.compile(r"^####\s+サブ(\d+):", re.MULTILINE)
_SUB_HEADER_EN_RE = re.compile(r"^####\s+[Ss]ub[ \t]+(\d+)[ \t]*:?", re.MULTILINE | re.IGNORECASE)
_MILESTONE_LABEL = "scope:milestone"


def _sub_plan_heading() -> str:
    return get_config().sections["sub_plan"]


def _has_sub_plan_h3(body: str) -> bool:
    plan = re.escape(_sub_plan_heading())
    return bool(re.search(rf"^###\s+{plan}\s*$", body, re.MULTILINE))


def _has_split_plan_pattern(body: str) -> bool:
    return bool(
        _has_sub_plan_h3(body)
        or _SUB_HEADER_RE.search(body)
        or _SUB_HEADER_EN_RE.search(body)
    )


def _sub_plan_in_section(section: str | None) -> bool:
    if not section:
        return False
    plan = re.escape(_sub_plan_heading())
    return bool(re.search(rf"^###\s+{plan}\s*$", section, re.MULTILINE))


class MilestoneConsistencyRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        violations: list[Violation] = []
        sections = get_config().sections
        has_plan = _has_split_plan_pattern(body)

        if has_plan and _MILESTONE_LABEL not in labels:
            violations.append(
                Violation(
                    rule_id="milestone_consistency.label_missing",
                    severity="fail",
                    message=(
                        "本文にサブイシュー分割計画パターンがあるのに "
                        f"{_MILESTONE_LABEL} が付いていません"
                    ),
                    location=None,
                    auto_fixable=True,
                    fix_hint="scope:milestone を付与",
                )
            )

        if _SUB_HEADER_EN_RE.search(body):
            violations.append(
                Violation(
                    rule_id="milestone_consistency.sub_header_english",
                    severity="fail",
                    message="英語形式のサブ見出し（#### Sub N:）が残っています",
                    location=None,
                    auto_fixable=True,
                    fix_hint="body_editor.normalize_sub_headers() を適用",
                )
            )

        design = get_section(body, sections["design"])
        milestone = get_section(body, sections["milestone"])
        if _sub_plan_in_section(design) and not _sub_plan_in_section(milestone):
            violations.append(
                Violation(
                    rule_id="milestone_consistency.sub_plan_misplaced",
                    severity="fail",
                    message=(
                        f"### {sections['sub_plan']} が ## {sections['design']} 配下にあり、"
                        f"## {sections['milestone']} 配下にありません"
                    ),
                    location=None,
                    auto_fixable=True,
                    fix_hint="body_editor.relocate_sub_plan() を適用",
                )
            )

        return violations


GATE_REGISTRY["milestone_consistency"] = MilestoneConsistencyRules
