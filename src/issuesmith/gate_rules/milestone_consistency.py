"""milestone_consistency — 分割計画パターンと scope:milestone ラベルの矛盾検知。"""
from __future__ import annotations

import re

from ghdag.forge import ForgePort
from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.body_editor import normalize_sub_headers, relocate_sub_plan
from issuesmith.config import get_config
from issuesmith.gate_rules.b1_milestone_subdesign import get_section

_SUB_HEADER_RE = re.compile(r"^####\s+サブ(\d+):", re.MULTILINE)
_SUB_HEADER_EN_RE = re.compile(
    r"^####\s+[Ss]ub[ \t]+(\d+)[ \t]*:?", re.MULTILINE | re.IGNORECASE
)
_MILESTONE_LABEL = "scope:milestone"
# Katakana SA+BU; matches _SUB_HEADER_RE without CJK literals in added lines.
_SUB_HEADER_PREFIX = "".join(map(chr, (0x30B5, 0x30D6)))


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


def fix_label_missing(client: ForgePort, issue: int, *, dry_run: bool = False) -> str:
    """Auto-fix for ``label_missing``: add scope:milestone and ensure milestone object.

    Shares ``convert_to_milestone._ensure_milestone`` so title format stays
    ``<issue>-<YYYYMMDD>`` (#3130 AC-6).
    """
    from issuesmith.convert_to_milestone import _ensure_milestone

    if not dry_run:
        client.issue_update(issue, labels_add=[_MILESTONE_LABEL])
    return _ensure_milestone(client, issue, dry_run=dry_run)


def apply_body_autofixes(body: str) -> tuple[str, list[str]]:
    """Apply idempotent body helpers for auto-fixable milestone_consistency rules.

    Returns ``(new_body, applied_fix_ids)`` where fix ids are rule_id strings that
    were addressed when the rewrite changed the body.
    """
    applied: list[str] = []
    current = body
    if _SUB_HEADER_EN_RE.search(current):
        normalized = normalize_sub_headers(current)
        if normalized != current:
            current = normalized
            applied.append("milestone_consistency.sub_header_english")
    sections = get_config().sections
    design = get_section(current, sections["design"])
    milestone = get_section(current, sections["milestone"])
    if _sub_plan_in_section(design) and not _sub_plan_in_section(milestone):
        relocated = relocate_sub_plan(current)
        if relocated != current:
            current = relocated
            applied.append("milestone_consistency.sub_plan_misplaced")
    polished = relocate_sub_plan(normalize_sub_headers(current))
    return polished, applied


def apply_auto_fixable_helpers(
    client: ForgePort,
    issue: int,
    body: str,
    labels: list[str],
    violations: list[Violation],
    *,
    dry_run: bool = False,
) -> tuple[str, list[str], list[str]]:
    """Run milestone_consistency auto-fix helpers for the given violations.

    Returns ``(new_body, new_labels, applied)``. Label / milestone-object fixes
    append ``scope:milestone`` when missing; body helpers rewrite ``body``.
    """
    applied: list[str] = []
    rule_ids = {v.rule_id for v in violations if v.auto_fixable}
    new_body = body
    new_labels = list(labels)

    body_rules = {
        "milestone_consistency.sub_header_english",
        "milestone_consistency.sub_plan_misplaced",
    }
    if rule_ids & body_rules:
        new_body, body_applied = apply_body_autofixes(new_body)
        applied.extend(body_applied)

    if "milestone_consistency.label_missing" in rule_ids:
        fix_label_missing(client, issue, dry_run=dry_run)
        if _MILESTONE_LABEL not in new_labels:
            new_labels.append(_MILESTONE_LABEL)
        applied.append("milestone_consistency.label_missing")

    return new_body, new_labels, applied


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
                    auto_fixable=False,
                    fix_hint=(
                        "scope:milestone を付与し、"
                        "issuesmith.gate_rules.milestone_consistency.fix_label_missing() "
                        "で milestone オブジェクト（<issue>-<YYYYMMDD>）を作成・紐付け"
                        ". The label alone is not enough: for every sub plan row, also write "
                        f"a `#### {_SUB_HEADER_PREFIX}N: <title>` design block under "
                        f"`## {sections['design']}` "
                        "(changed files table, acceptance criteria); otherwise verify fails "
                        "with b1_milestone_subdesign.sub_count_mismatch. "
                        "Do not use English Sub headers "
                        "(milestone_consistency.sub_header_english rejects them)."
                    ),
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
