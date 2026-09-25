"""B1 Issue size gate (nexus #3665).

Judges an Issue's size from its own change table (no clone is read, unlike
``scope_breadth``): counted files, concerns (distinct parent directories of
counted files) and whether deletion and creation are mixed. An oversized
Issue fails with a fix_hint that proposes a sub-issue split plan; the gate
never rewrites the body itself.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import extract_change_table_rows

_MILESTONE_LABEL = "scope:milestone"


@dataclass(frozen=True)
class SizeMeasure:
    files: tuple[str, ...]                 # counted paths (table order)
    concerns: dict[str, tuple[str, ...]]   # parent dir -> paths (table order)
    kinds: frozenset[str]                  # subset of {"delete", "new", "modify"}


def _normalize_kind(change_type: str) -> str:
    lowered = change_type.lower()
    if "削除" in change_type or "delete" in lowered:
        return "delete"
    if "新規" in change_type or any(k in lowered for k in ("new", "add")):
        return "new"
    return "modify"


def _counted_rows(body: str, exclude_prefixes: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return ``(path, kind)`` for counted rows, deduplicated by ``(repo, path)``."""
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str]] = []
    for repo, path, change_type in extract_change_table_rows(body):
        if (repo, path) in seen:
            continue
        seen.add((repo, path))
        if path.startswith(exclude_prefixes):
            continue
        rows.append((path, _normalize_kind(change_type)))
    return rows


def measure_size(body: str, exclude_prefixes: tuple[str, ...]) -> SizeMeasure:
    rows = _counted_rows(body, exclude_prefixes)
    concerns: dict[str, list[str]] = {}
    for path, _ in rows:
        concerns.setdefault(posixpath.dirname(path) or ".", []).append(path)
    return SizeMeasure(
        files=tuple(path for path, _ in rows),
        concerns={d: tuple(paths) for d, paths in concerns.items()},
        kinds=frozenset(kind for _, kind in rows),
    )


def _fix_hint(body: str, measure: SizeMeasure) -> str:
    sections = get_config().sections
    try:
        target_repo = str(parse_issue_metadata(body).get("target_repo") or "").strip()
    except Exception:
        target_repo = ""
    lines = [
        f"本文に `## {sections['milestone']}` > `### {sections['sub_plan']}` を足して、"
        "関心事（親ディレクトリ）ごとにサブイシューへ分割する。例:",
        "",
        "| # | タイトル | 対象リポジトリ | 内容 | 依存 |",
        "|---|---|---|---|---|",
    ]
    for i, (concern, paths) in enumerate(measure.concerns.items(), start=1):
        lines.append(f"| {i} | {concern} | {target_repo} | {', '.join(paths)} | なし |")
    return "\n".join(lines)


class ScopeSizeRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if _MILESTONE_LABEL in labels:
            return []
        cfg = get_config().scope_size
        if not cfg.enabled:
            return []
        if not extract_change_table_rows(body):
            return []

        rows = _counted_rows(body, cfg.exclude_prefixes)
        measure = measure_size(body, cfg.exclude_prefixes)
        fix_hint = _fix_hint(body, measure)

        def _violation(rule_id: str, message: str) -> Violation:
            return Violation(
                rule_id=rule_id,
                severity="fail",
                message=message,
                location=None,
                auto_fixable=False,
                fix_hint=fix_hint,
            )

        violations: list[Violation] = []
        if len(measure.files) > cfg.max_files:
            violations.append(
                _violation(
                    "scope_size.too_many_files",
                    f"Issue changes too many files: files={len(measure.files)}/{cfg.max_files}",
                )
            )
        if len(measure.concerns) > cfg.max_concerns:
            dirs = ", ".join(measure.concerns)
            violations.append(
                _violation(
                    "scope_size.too_many_concerns",
                    "Issue spans too many concerns: "
                    f"concerns={len(measure.concerns)}/{cfg.max_concerns} ({dirs})",
                )
            )
        if not cfg.delete_with_new and {"delete", "new"} <= measure.kinds:
            deleted = ", ".join(p for p, k in rows if k == "delete")
            created = ", ".join(p for p, k in rows if k == "new")
            violations.append(
                _violation(
                    "scope_size.delete_with_new",
                    f"Issue mixes deletion and creation: delete=[{deleted}] new=[{created}]",
                )
            )
        return violations


GATE_REGISTRY["scope_size"] = ScopeSizeRules
