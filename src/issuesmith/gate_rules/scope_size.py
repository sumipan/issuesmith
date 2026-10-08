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
from issuesmith.contract import change_paths_for_repo, extract_change_table_rows
from issuesmith.gate_rules.b1_milestone_subdesign import (
    change_rows_with_content,
    find_dependency_cycles,
    infer_sub_dependencies,
)

_MILESTONE_LABEL = "scope:milestone"
_SPLIT_CONTENT = "split from oversized issue"
_MAX_SLICES = 3
_REFERENCE_SUFFIXES = frozenset({".yaml", ".yml", ".toml", ".json", ".ini", ".cfg"})
_DECLINED_HINT = (
    "Split yields {n} slices (max {max}); not auto-promoted. "
    "Split the Issue, or write the sub plan by hand."
)

# (repo, path, change type, change content) of one parent change-table row.
_Row = tuple[str, str, str, str]


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


def _concern_display_name(concern: str) -> str:
    """Display name for a concern key; root-level files (key ``.``) show as ``root``."""
    return "root" if concern == "." else concern


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


def _fix_hint(body: str, cfg: ScopeSizeConfig) -> str:
    sections = get_config().sections
    if promotion_declined(body, cfg):
        target_repo, concerns, deps = _split_plan(body, cfg)
        lines = [
            _DECLINED_HINT.format(n=len(concerns), max=_MAX_SLICES),
            f"That helper adds `## {sections['milestone']}` > `### {sections['sub_plan']}`, "
            f"`#### {_sub_header_prefix()}N: <title>` blocks under `## {sections['design']}` with "
            + ", ".join(f"**{name}**" for name in get_config().sub_design_subsections)
            + f", {_MILESTONE_LABEL}, and the GitHub milestone object. "
            "b1_milestone_subdesign fails when the plan row count and the sub header "
            "count differ. Example plan:",
            "",
            cfg.sub_plan_header,
            "|---|---|---|---|---|",
        ]
        lines.extend(_plan_rows(target_repo, concerns, deps, cfg))
        return "\n".join(lines)
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
    lines.extend(_plan_rows(*_split_plan(body, cfg), cfg))
    return "\n".join(lines)


def _is_test_row(path: str) -> bool:
    return path.startswith("tests/")


def _is_reference_row(path: str, cfg: ScopeSizeConfig) -> bool:
    if _is_test_row(path):
        return False
    for prefix in cfg.exclude_prefixes:
        if path.startswith(prefix):
            return True
    if "/" not in path:
        return True
    _, ext = posixpath.splitext(path)
    return ext.lower() in _REFERENCE_SUFFIXES


def _test_mirror_dir(path: str) -> str:
    """Directory under ``tests/`` used to mirror-test-match core slices."""
    if not path.startswith("tests/"):
        return ""
    rest = path[len("tests/") :]
    segments = rest.split("/")
    parts: list[str] = []
    for part in segments[:-1]:
        if "*" in part:
            break
        parts.append(part)
    return "/".join(parts)


def _path_stem(path: str) -> str:
    base = posixpath.basename(path)
    name, _ext = posixpath.splitext(base)
    if name.startswith("test_"):
        return name[len("test_") :]
    return name or base


def _core_parent(path: str) -> str:
    return posixpath.dirname(path) or "."


def _iter_slice_cores(
    rows: list[_Row], cfg: ScopeSizeConfig
) -> list[tuple[str, str]]:
    return [
        (path, content)
        for _, path, _, content in rows
        if _row_is_core(path, cfg)
    ]


def _test_belongs_to_core(test_path: str, core_path: str) -> bool:
    mirror = _test_mirror_dir(test_path)
    parent = _core_parent(core_path)
    if mirror and (parent == mirror or parent.endswith("/" + mirror)):
        return True
    return _path_stem(test_path) == _path_stem(core_path)


def _match_slice_for_test(
    path: str,
    slices: list[tuple[str, list[_Row]]],
    cfg: ScopeSizeConfig,
) -> int:
    mirror = _test_mirror_dir(path)
    best_idx = -1
    best_len = -1
    if mirror:
        for idx, (_, rows) in enumerate(slices):
            for core_path, _ in _iter_slice_cores(rows, cfg):
                parent = _core_parent(core_path)
                if parent == mirror or parent.endswith("/" + mirror):
                    if len(mirror) > best_len:
                        best_len = len(mirror)
                        best_idx = idx
    if best_idx >= 0:
        return best_idx
    stem = _path_stem(path)
    for idx, (_, rows) in enumerate(slices):
        for core_path, _ in _iter_slice_cores(rows, cfg):
            if _path_stem(core_path) == stem:
                return idx
    return 0


def _match_slice_for_reference(
    content: str,
    slices: list[tuple[str, list[_Row]]],
    cfg: ScopeSizeConfig,
) -> int:
    for idx, (_, rows) in enumerate(slices):
        for core_path, _ in _iter_slice_cores(rows, cfg):
            if core_path in content:
                return idx
    for idx, (_, rows) in enumerate(slices):
        for core_path, _ in _iter_slice_cores(rows, cfg):
            if _path_stem(core_path) in content:
                return idx
    return 0


def _row_is_core(path: str, cfg: ScopeSizeConfig) -> bool:
    return not _is_test_row(path) and not _is_reference_row(path, cfg)


def _rows_by_slice(body: str, cfg: ScopeSizeConfig) -> dict[str, list[_Row]]:
    """Group change-table rows into promotion slices (file-union complete).

    Unlike :func:`measure_size`, excluded prefixes are kept so promoted sub
    designs cover the full parent change table. Rows keep the change-content
    column so sub designs (and dependency inference) see it (#4825).
    """
    seen: set[tuple[str, str]] = set()
    core_rows: list[_Row] = []
    test_rows: list[_Row] = []
    reference_rows: list[_Row] = []
    for repo, path, change_type, content in change_rows_with_content(body):
        key = (repo, path)
        if key in seen:
            continue
        seen.add(key)
        row: _Row = (repo, path, change_type, content)
        if _is_test_row(path):
            test_rows.append(row)
        elif _is_reference_row(path, cfg):
            reference_rows.append(row)
        else:
            core_rows.append(row)

    core_paths = [path for _, path, _, _ in core_rows]
    concern_groups = _group_paths_by_concern(core_paths)

    named_slices: list[tuple[str, list[_Row]]] = []
    assigned_cores: set[tuple[str, str]] = set()
    for concern_key, paths in concern_groups.items():
        slice_rows: list[_Row] = []
        for path in paths:
            for row in core_rows:
                if row[1] == path and (row[0], row[1]) not in assigned_cores:
                    slice_rows.append(row)
                    assigned_cores.add((row[0], row[1]))
        if slice_rows:
            named_slices.append((_concern_display_name(concern_key), slice_rows))

    for row in core_rows:
        if (row[0], row[1]) not in assigned_cores:
            parent = _concern_display_name(_core_parent(row[1]))
            named_slices.append((parent, [row]))
            assigned_cores.add((row[0], row[1]))

    if not named_slices:
        return {}

    for row in test_rows:
        idx = _match_slice_for_test(row[1], named_slices, cfg)
        named_slices[idx][1].append(row)

    for row in reference_rows:
        idx = _match_slice_for_reference(row[3], named_slices, cfg)
        named_slices[idx][1].append(row)

    for slice_idx, (_, slice_rows) in enumerate(named_slices):
        deleted_cores = [
            path
            for _, path, change_type, _ in slice_rows
            if _row_is_core(path, cfg) and _normalize_kind(change_type, cfg) == "delete"
        ]
        if not deleted_cores:
            continue
        delete_stems = [_path_stem(p) for p in deleted_cores]
        for other_idx, (_, other_rows) in enumerate(named_slices):
            if other_idx == slice_idx:
                continue
            kept: list[_Row] = []
            for row in other_rows:
                _, path, _, content = row
                if _row_is_core(path, cfg) or _is_reference_row(path, cfg):
                    if any(ds in content or ds == _path_stem(path) for ds in delete_stems):
                        named_slices[slice_idx][1].append(row)
                        continue
                kept.append(row)
            named_slices[other_idx] = (named_slices[other_idx][0], kept)
        for test_row in list(test_rows):
            for deleted_path in deleted_cores:
                if not _test_belongs_to_core(test_row[1], deleted_path):
                    continue
                for idx, (name, rows) in enumerate(named_slices):
                    if test_row in rows and idx != slice_idx:
                        named_slices[idx] = (name, [r for r in rows if r != test_row])
                        named_slices[slice_idx][1].append(test_row)
                break

    filtered: list[tuple[str, list[_Row]]] = []
    orphans: list[_Row] = []
    for name, rows in named_slices:
        if any(_row_is_core(r[1], cfg) for r in rows):
            filtered.append((name, rows))
        else:
            orphans.extend(rows)
    if not filtered:
        return {}
    if orphans:
        filtered[0][1].extend(orphans)

    return {name: rows for name, rows in filtered}


def promotion_declined(body: str, cfg: ScopeSizeConfig | None = None) -> bool:
    """True when auto-promotion would yield zero slices or exceed ``_MAX_SLICES``."""
    cfg = cfg or get_config().scope_size
    _, concerns, _ = _split_plan(body, cfg)
    n = len(concerns)
    return n == 0 or n > _MAX_SLICES


def _build_sub_block(
    num: int,
    concern: str,
    rows: list[_Row],
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
    paths = [path for _, path, _, _ in rows]
    for repo, path, change_type, content in rows:
        repo_cell = repo or target_repo
        table_lines.append(
            f"| `{repo_cell}` | `{path}` | {change_type or 'modify'} "
            f"| {content or _SPLIT_CONTENT} |"
        )
    yaml_paths = "\n".join(f"  - {p}" for p in paths) or "  - []"
    concern = _concern_display_name(concern)
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


def _merge_unreadable_concerns(
    concerns: dict[str, list[_Row]],
    *,
    target_repo: str,
    subsections: tuple[str, ...],
    changed_label: str,
    ac_label: str,
) -> dict[str, list[_Row]]:
    """Fold concerns whose sub block SUB1 cannot read into a readable one (nexus #4852).

    Each concern's sub block is read back with :func:`change_paths_for_repo`
    (the SUB1 / B1 extraction). Rows of an unreadable concern move to the first
    readable concern; when none is readable, a single ``.`` concern keeps every
    row so the parent change table stays file-union complete.
    """
    readable: dict[str, list[_Row]] = {}
    orphans: list[_Row] = []
    for concern, rows in concerns.items():
        block = _build_sub_block(
            1,
            concern,
            rows,
            target_repo=target_repo,
            subsections=subsections,
            changed_label=changed_label,
            ac_label=ac_label,
        )
        if change_paths_for_repo(block, target_repo):
            readable[concern] = list(rows)
        else:
            orphans.extend(rows)
    if not orphans:
        return concerns
    if not readable:
        return {".": orphans}
    first = next(iter(readable))
    readable[first].extend(orphans)
    return readable


def _merge_dependency_cycles(
    concerns: list[tuple[str, list[_Row]]],
    *,
    target_repo: str,
) -> tuple[list[tuple[str, list[_Row]]], dict[int, set[int]]]:
    """Fold each strongly connected component of the inferred graph into one sub (#4825).

    Returns the acyclic concern list and its ``consumer -> creators`` dependencies.
    """
    sections = get_config().sections
    while True:
        blocks = [
            (
                num,
                _build_sub_block(
                    num,
                    concern,
                    rows,
                    target_repo=target_repo,
                    subsections=get_config().sub_design_subsections,
                    changed_label=sections["changed_files"],
                    ac_label=sections["acceptance_criteria"],
                ),
            )
            for num, (concern, rows) in enumerate(concerns, start=1)
        ]
        deps = infer_sub_dependencies(blocks)
        cycles = find_dependency_cycles(deps)
        if not cycles:
            return concerns, deps
        component_of = {num: comp for comp in cycles for num in comp}
        merged: list[tuple[str, list[_Row]]] = []
        for num, (concern, rows) in enumerate(concerns, start=1):
            comp = component_of.get(num)
            if comp is None:
                merged.append((concern, rows))
            elif num == comp[0]:
                merged.append((
                    " + ".join(_concern_display_name(concerns[n - 1][0]) for n in comp),
                    [row for n in comp for row in concerns[n - 1][1]],
                ))
        concerns = merged


def _split_plan(
    body: str, cfg: ScopeSizeConfig
) -> tuple[str, list[tuple[str, list[_Row]]], dict[int, set[int]]]:
    """``(target_repo, numbered concerns, dependencies)`` of the promotion split plan."""
    sections = get_config().sections
    try:
        target_repo = str(parse_issue_metadata(body).get("target_repo") or "").strip()
    except Exception:
        target_repo = ""
    concerns = _rows_by_slice(body, cfg)
    if target_repo and concerns:
        concerns = _merge_unreadable_concerns(
            concerns,
            target_repo=target_repo,
            subsections=get_config().sub_design_subsections,
            changed_label=sections["changed_files"],
            ac_label=sections["acceptance_criteria"],
        )
    merged, deps = _merge_dependency_cycles(list(concerns.items()), target_repo=target_repo)
    return target_repo, merged, deps


def _plan_rows(
    target_repo: str,
    concerns: list[tuple[str, list[_Row]]],
    deps: dict[int, set[int]],
    cfg: ScopeSizeConfig,
) -> list[str]:
    """Sub-plan data rows; depends-on lists creators as ``#N`` (ascending)."""
    lines: list[str] = []
    for num, (concern, rows) in enumerate(concerns, start=1):
        paths = ", ".join(path for _, path, _, _ in rows)
        creators = sorted(deps.get(num, set()))
        dep_cell = ", ".join(f"#{n}" for n in creators) or cfg.no_deps_word
        lines.append(
            f"| {num} | {_concern_display_name(concern)} | {target_repo} "
            f"| {paths} | {dep_cell} |"
        )
    return lines


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

    target_repo, concerns, deps = _split_plan(body, cfg)

    design = get_section(body, design_name) or ""
    existing_subs = SUB_HEADER_RE.findall(design)
    if concerns and len(existing_subs) >= len(concerns):
        return relocate_sub_plan(normalize_sub_headers(body))

    if promotion_declined(body, cfg):
        return body

    if not concerns:
        return relocate_sub_plan(normalize_sub_headers(body))

    plan_lines = [
        f"### {plan_name}",
        cfg.sub_plan_header,
        "|---|---|---|---|---|",
        *_plan_rows(target_repo, concerns, deps, cfg),
    ]
    sub_blocks: list[str] = []
    for i, (concern, rows) in enumerate(concerns, start=1):
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
        fix_hint = _fix_hint(body, cfg)

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
            dirs = ", ".join(_concern_display_name(c) for c in measure.concerns)
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
