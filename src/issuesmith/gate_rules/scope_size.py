"""B1 Issue size gate (nexus #3665).

Judges an Issue's size from its own change table (no clone is read, unlike
``scope_breadth``): counted files, concerns (distinct parent directories of
counted files) and whether deletion and creation are mixed. An oversized
Issue fails with a fix_hint that proposes a sub-issue split plan; the gate
never rewrites the body itself. Recovery promotes oversized Issues via
:func:`promote_oversized_issue_body` (nexus #4191).
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import ScopeSizeConfig, get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import extract_change_table_rows

_MILESTONE_LABEL = "scope:milestone"


def _sub_header_prefix() -> str:
    """``#### <prefix>N:`` sub-design header prefix from the language pack."""
    return get_config().language.sub_header_prefix


def _change_table_header() -> str:
    """4-column change-table header row (b1_milestone_subdesign schema) from the pack."""
    return "| " + " | ".join(get_config().language.change_table_columns) + " |"


@dataclass(frozen=True)
class SizeMeasure:
    files: tuple[str, ...]                 # counted paths (table order)
    concerns: dict[str, tuple[str, ...]]   # parent dir -> paths (table order)
    kinds: frozenset[str]                  # subset of {"delete", "new", "modify"}


def _normalize_kind(change_type: str, cfg: ScopeSizeConfig) -> str:
    """Map a change-table kind cell to ``delete`` / ``new`` / ``modify``.

    The kind vocabulary comes from ``scope_size.delete_words`` / ``new_words``
    (English defaults; hosts writing Issues in another language configure theirs).
    """
    lowered = change_type.lower()
    if any(word in lowered for word in cfg.delete_words):
        return "delete"
    if any(word in lowered for word in cfg.new_words):
        return "new"
    return "modify"


def _common_prefix(strings: list[str]) -> str:
    if not strings:
        return ""
    prefix = strings[0]
    for name in strings[1:]:
        limit = min(len(prefix), len(name))
        i = 0
        while i < limit and prefix[i] == name[i]:
            i += 1
        prefix = prefix[:i]
    return prefix


def _sibling_dirs_share_prefix(parent_dirs: tuple[str, ...]) -> bool:
    """True when sibling directory basenames share a hyphenated common prefix."""
    basenames = [posixpath.basename(parent) for parent in parent_dirs]
    if len(basenames) < 2:
        return False
    prefix = _common_prefix(basenames)
    return len(prefix) >= 2 and prefix.endswith("-")


def _group_paths_by_concern(paths: tuple[str, ...] | list[str]) -> dict[str, tuple[str, ...]]:
    """Map concern directory keys to counted file paths (table order preserved)."""
    parent_to_paths: dict[str, list[str]] = {}
    for path in paths:
        if "/" not in path:
            continue
        parent = posixpath.dirname(path) or "."
        parent_to_paths.setdefault(parent, []).append(path)

    concerns: dict[str, list[str]] = {}
    singleton_parents: dict[str, list[str]] = {}
    for parent, grouped in parent_to_paths.items():
        if len(grouped) >= 2:
            concerns[parent] = grouped
        else:
            singleton_parents[parent] = grouped

    by_grandparent: dict[str, list[str]] = {}
    for parent in singleton_parents:
        grandparent = posixpath.dirname(parent) or "."
        by_grandparent.setdefault(grandparent, []).append(parent)

    for grandparent, parents in by_grandparent.items():
        parent_tuple = tuple(parents)
        if len(parents) >= 2 and _sibling_dirs_share_prefix(parent_tuple):
            merged: list[str] = []
            for parent in parents:
                merged.extend(singleton_parents[parent])
            concerns[grandparent] = concerns.get(grandparent, []) + merged
        else:
            for parent in parents:
                concerns[parent] = singleton_parents[parent]

    return {key: tuple(value) for key, value in concerns.items()}


def _counted_rows(body: str, cfg: ScopeSizeConfig) -> list[tuple[str, str]]:
    """Return ``(path, kind)`` for counted rows, deduplicated by ``(repo, path)``."""
    seen: set[tuple[str, str]] = set()
    rows: list[tuple[str, str]] = []
    for repo, path, change_type in extract_change_table_rows(body):
        if (repo, path) in seen:
            continue
        seen.add((repo, path))
        if path.startswith(cfg.exclude_prefixes):
            continue
        rows.append((path, _normalize_kind(change_type, cfg)))
    return rows


def measure_size(body: str, cfg: ScopeSizeConfig | None = None) -> SizeMeasure:
    cfg = cfg or get_config().scope_size
    rows = _counted_rows(body, cfg)
    paths = [path for path, _ in rows]
    return SizeMeasure(
        files=tuple(paths),
        concerns=_group_paths_by_concern(paths),
        kinds=frozenset(kind for _, kind in rows),
    )


def _fix_hint(body: str, measure: SizeMeasure, cfg: ScopeSizeConfig) -> str:
    sections = get_config().sections
    try:
        target_repo = str(parse_issue_metadata(body).get("target_repo") or "").strip()
    except Exception:
        target_repo = ""
    lines = [
        "Run issuesmith.b1_verify.apply_deterministic_recovery() (or "
        "issuesmith.gate_rules.scope_size.promote_oversized_issue_body() then "
        "milestone_consistency.fix_label_missing() + body_editor.normalize_sub_headers() "
        "+ body_editor.relocate_sub_plan()). Do not hand-edit English Sub headers.",
        f"That helper adds `## {sections['milestone']}` > `### {sections['sub_plan']}`, "
        f"`#### {_sub_header_prefix()}N: <title>` blocks under `## {sections['design']}` with "
        + ", ".join(f"**{name}**" for name in get_config().sub_design_subsections)
        + ", scope:milestone, and the GitHub milestone object. "
        "b1_milestone_subdesign fails when the plan row count and the sub header "
        "count differ. Example plan:",
        "",
        cfg.sub_plan_header,
        "|---|---|---|---|---|",
    ]
    for i, (concern, paths) in enumerate(measure.concerns.items(), start=1):
        lines.append(
            f"| {i} | {concern} | {target_repo} | {', '.join(paths)} | {cfg.no_deps_word} |"
        )
    return "\n".join(lines)


def _rows_by_concern(
    body: str, cfg: ScopeSizeConfig
) -> dict[str, list[tuple[str, str, str]]]:
    """Group every change-table row by parent directory (file-union complete).

    Unlike :func:`measure_size`, excluded prefixes are kept so promoted sub
    designs cover the full parent change table.
    """
    seen: set[tuple[str, str]] = set()
    by_parent: dict[str, list[tuple[str, str, str]]] = {}
    for repo, path, change_type in extract_change_table_rows(body):
        key = (repo, path)
        if key in seen:
            continue
        seen.add(key)
        parent = posixpath.dirname(path) or "."
        by_parent.setdefault(parent, []).append((repo, path, change_type))
    measured = measure_size(body, cfg)
    ordered: dict[str, list[tuple[str, str, str]]] = {}
    consumed: set[str] = set()
    for concern, paths in measured.concerns.items():
        rows_for_concern: list[tuple[str, str, str]] = []
        for path in paths:
            parent = posixpath.dirname(path) or "."
            if parent in consumed:
                continue
            consumed.add(parent)
            rows_for_concern.extend(by_parent.get(parent, []))
        if rows_for_concern:
            ordered[concern] = rows_for_concern
    # Excluded prefixes and root-level files are not measured concerns but must
    # stay in the promoted sub designs (file-union complete).
    for parent, rows in by_parent.items():
        if parent not in consumed:
            ordered.setdefault(parent, []).extend(rows)
    return ordered


def _build_sub_block(
    num: int,
    concern: str,
    rows: list[tuple[str, str, str]],
    *,
    target_repo: str,
    subsections: tuple[str, ...],
    changed_label: str,
    ac_label: str,
) -> str:
    # Column names match b1_milestone_subdesign._check_table_schema expectations.
    table_lines = [
        f"**{changed_label}**:",
        _change_table_header(),
        "|---|---|---|---|",
    ]
    paths = [path for _, path, _ in rows]
    for repo, path, change_type in rows:
        repo_cell = repo or target_repo
        table_lines.append(
            f"| `{repo_cell}` | `{path}` | {change_type or 'modify'} "
            "| split from oversized issue |"
        )
    yaml_paths = "\n".join(f"  - {p}" for p in paths) or "  - []"
    sub_parts = [f"#### {_sub_header_prefix()}{num}: {concern}", ""]
    for name in subsections:
        if name == changed_label:
            sub_parts.extend(table_lines)
            sub_parts.append("")
        elif name == ac_label:
            sub_parts.append(f"**{ac_label}**:")
            sub_parts.append("```yaml")
            sub_parts.append("paths_must_exist:")
            sub_parts.append(yaml_paths)
            sub_parts.append("```")
            sub_parts.append(
                f"- [ ] Concern `{concern}` change table lists every assigned path"
            )
            sub_parts.append(
                f"- [ ] Sub design `{num}` includes the required subsections"
            )
            sub_parts.append(
                f"- [ ] Parent change-file union includes every path under `{concern}`"
            )
            sub_parts.append("")
        else:
            sub_parts.append(f"**{name}**: Split work for concern `{concern}`")
            sub_parts.append("")
    return "\n".join(sub_parts).rstrip() + "\n"


def _upsert_preserving_preamble(body: str, heading: str, content: str) -> str:
    """Like upsert_section but keep text before the first H2 (yaml metadata)."""
    from issuesmith.body_editor import upsert_section

    match = re.search(r"^##\s", body, re.MULTILINE)
    if not match:
        return upsert_section(body, heading, content)
    preamble = body[: match.start()]
    return preamble + upsert_section(body[match.start() :], heading, content)


def promote_oversized_issue_body(body: str, cfg: ScopeSizeConfig | None = None) -> str:
    """Rewrite an oversized Issue body into a milestone split plan + sub designs.

    Idempotent when a SUB_HEADER_RE block already exists for every concern row.
    Does not touch labels or the GitHub milestone object — callers
    must run :func:`issuesmith.gate_rules.milestone_consistency.fix_label_missing`
    (or use :func:`issuesmith.b1_verify.apply_deterministic_recovery`).
    """
    from issuesmith.body_editor import (
        get_section,
        normalize_sub_headers,
        relocate_sub_plan,
    )
    from issuesmith.contract import SUB_HEADER_RE

    cfg = cfg or get_config().scope_size
    sections = get_config().sections
    design_name = sections["design"]
    milestone_name = sections["milestone"]
    plan_name = sections["sub_plan"]
    changed_label = sections["changed_files"]
    ac_label = sections["acceptance_criteria"]
    subsections = get_config().sub_design_subsections

    concerns = _rows_by_concern(body, cfg)
    if not concerns:
        return relocate_sub_plan(normalize_sub_headers(body))

    design = get_section(body, design_name) or ""
    existing_subs = SUB_HEADER_RE.findall(design)
    if len(existing_subs) >= len(concerns):
        return relocate_sub_plan(normalize_sub_headers(body))

    try:
        target_repo = str(parse_issue_metadata(body).get("target_repo") or "").strip()
    except Exception:
        target_repo = ""

    plan_lines = [
        f"### {plan_name}",
        cfg.sub_plan_header,
        "|---|---|---|---|---|",
    ]
    sub_blocks: list[str] = []
    for i, (concern, rows) in enumerate(concerns.items(), start=1):
        paths = ", ".join(path for _, path, _ in rows)
        plan_lines.append(
            f"| {i} | {concern} | {target_repo} | {paths} | {cfg.no_deps_word} |"
        )
        sub_blocks.append(
            _build_sub_block(
                i,
                concern,
                rows,
                target_repo=target_repo,
                subsections=subsections,
                changed_label=changed_label,
                ac_label=ac_label,
            )
        )

    sub_match = SUB_HEADER_RE.search(design)
    design_without_subs = (
        design[: sub_match.start()].rstrip() if sub_match else design.rstrip()
    )
    new_design = (
        f"{design_without_subs}\n\n" if design_without_subs else ""
    ) + "\n".join(sub_blocks)

    body = _upsert_preserving_preamble(body, design_name, new_design.strip("\n"))
    milestone_content = "\n".join(plan_lines)
    existing_milestone = get_section(body, milestone_name)
    if existing_milestone is None:
        body = _upsert_preserving_preamble(body, milestone_name, milestone_content)
    elif not re.search(
        rf"^###\s+{re.escape(plan_name)}\s*$", existing_milestone, re.MULTILINE
    ):
        merged = f"{existing_milestone.rstrip()}\n\n{milestone_content}".strip("\n")
        body = _upsert_preserving_preamble(body, milestone_name, merged)
    else:
        body = _upsert_preserving_preamble(body, milestone_name, milestone_content)

    # Parent AC section is required by b1_ac_format once the Issue is a milestone.
    # Leave paths_must_exist empty: cp1.milestone.paths_must_exist_unmapped only
    # recognizes host-language change-type cells (not English Modify/Add), so
    # copying English "Modify"/"Add" rows would always fail. LLM recovery fills
    # concrete paths.
    if get_section(body, ac_label) is None:
        ac_content = (
            "```yaml\n"
            "paths_must_exist: []\n"
            "```\n"
        )
        body = _upsert_preserving_preamble(body, ac_label, ac_content)

    return relocate_sub_plan(normalize_sub_headers(body))


class ScopeSizeRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if _MILESTONE_LABEL in labels:
            return []
        cfg = get_config().scope_size
        if not cfg.enabled:
            return []
        if not extract_change_table_rows(body):
            return []

        rows = _counted_rows(body, cfg)
        measure = measure_size(body, cfg)
        fix_hint = _fix_hint(body, measure, cfg)

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
