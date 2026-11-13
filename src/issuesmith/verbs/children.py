"""Child-issue verbs — split plan, create, validate, allow_paths widen (#6583 / nexus #6477)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from ghdag.forge import ForgePort

from issuesmith.body_editor import (
    count_heading,
    get_section,
    get_section_by_keyword,
    get_subsections,
    replace_allow_paths,
    split_h2_sections,
)
from issuesmith.config import get_config
from issuesmith.contract import parse_issue_metadata
from issuesmith.convert_to_milestone import _ensure_milestone
from issuesmith.milestone import (
    _SUB1_DEP_REF_RE,
    Sub1State,
    _list_chain_children,
    allow_paths_for_row,
    build_child_body,
    ensure_sub1_binding,
    parse_split_plan,
    prevalidate_child_body,
    resolve_dependencies,
)
from issuesmith.milestone import (
    validate_children as _validate_children,
)
from issuesmith.pr_scope import normalize_allow_path
from issuesmith.queue_triage import parse_frontmatter_fields

__all__ = [
    "ChildPlan",
    "ChildPlanEntry",
    "ChildPlanError",
    "create_child",
    "plan_children",
    "validate_children",
    "widen_allow_paths",
]


@dataclass(frozen=True, slots=True)
class ChildPlanEntry:
    row_number: int
    title: str
    repo: str
    existing: int | None
    dependencies: tuple[int, ...]
    allow_paths: tuple[str, ...]
    draft_body: str


@dataclass(frozen=True, slots=True)
class ChildPlan:
    entries: tuple[ChildPlanEntry, ...]
    milestone_number: int | None


class ChildPlanError(RuntimeError):
    def __init__(self, violations: Sequence[str]) -> None:
        self.violations = tuple(violations)
        message = "; ".join(self.violations) if self.violations else "child plan failed"
        super().__init__(message)


def _safe_metadata(body: str) -> dict[str, Any]:
    try:
        return parse_issue_metadata(body)
    except ValueError:
        return parse_frontmatter_fields(body) or {}


def _milestone_number(issue: dict[str, Any]) -> int | None:
    milestone = issue.get("milestone")
    if isinstance(milestone, dict):
        number = milestone.get("number")
        if isinstance(number, int):
            return number
    return None


def _label_names(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels") or []
    names: list[str] = []
    for item in labels:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names


def _issue_numbers_from_resolved(
    resolved: str,
    *,
    row_to_issue: dict[int, int],
    table_row_count: int,
) -> tuple[int, ...]:
    lang = get_config().language
    dep = (resolved or "").strip()
    if not dep or dep == lang.no_deps_word:
        return ()
    nums: list[int] = []
    issue_ids = set(row_to_issue.values())
    for token in _SUB1_DEP_REF_RE.findall(dep):
        number = int(token)
        if number in row_to_issue:
            nums.append(row_to_issue[number])
        elif number in issue_ids:
            nums.append(number)
        elif number > table_row_count:
            nums.append(number)
    return tuple(dict.fromkeys(nums))


def _parent_plan_gate(body: str) -> list[str]:
    cfg = get_config()
    sections = cfg.sections
    design = sections["design"]
    ac = sections["acceptance_criteria"]
    violations: list[str] = []
    if count_heading(body, design) > 1:
        violations.append(f"duplicate ## {design} section")
        return violations
    design_text = get_section(body, design)
    ac_text = get_section(body, ac) or get_section_by_keyword(body, ac)
    if not design_text or not design_text.strip():
        violations.append(f"missing ## {design}")
    if not ac_text or not ac_text.strip():
        violations.append(f"missing ## {ac}")
    sub_prefix = f"#### {cfg.language.sub_header_prefix}"
    if not get_subsections(body, design, sub_prefix):
        violations.append(f"missing {sub_prefix} blocks under ## {design}")
    return violations


def _find_first_h2_pos(body: str) -> int | None:
    pos = 0
    for line in body.splitlines(keepends=True):
        stripped = line.rstrip("\n\r")
        if stripped.startswith("## ") and not stripped.startswith("###"):
            return pos
        pos += len(line)
    return None


def _extract_preamble(body: str) -> str:
    pos = _find_first_h2_pos(body)
    if pos is None:
        return body
    return body[:pos]


def _dependency_section(body: str, dep_heading: str) -> str | None:
    target = f"## {dep_heading}"
    for heading, section in split_h2_sections(body):
        if heading.rstrip() == target:
            return section
    return None


def _restore_deterministic_parts(original: str, polished: str, *, dep_heading: str) -> str:
    """Align polished preamble and dependency section with *original* (post-LLM polish)."""
    polished_sections = split_h2_sections(polished)
    if not polished_sections:
        return original

    target = f"## {dep_heading}"
    kept_sections = [
        (heading, section)
        for heading, section in polished_sections
        if heading.rstrip() != target
    ]

    preamble = _extract_preamble(original)
    dep_section = _dependency_section(original, dep_heading)

    parts: list[str] = [preamble]
    if dep_section:
        parts.append(dep_section)
    for _, section in kept_sections:
        parts.append(section)
    return "".join(parts)


def plan_children(client: ForgePort, parent_number: int, *, repo: str) -> ChildPlan:
    """Parse the parent split plan and materialize :class:`ChildPlanEntry` rows."""
    parent = client.issue_get(
        parent_number,
        fields=["number", "body", "labels", "milestone", "title"],
    )
    parent_body = str(parent.get("body") or "")
    violations = _parent_plan_gate(parent_body)
    if violations:
        raise ChildPlanError(violations)

    if _milestone_number(parent) is None:
        try:
            _ensure_milestone(client, parent_number, dry_run=False)
            parent = client.issue_get(
                parent_number,
                fields=["number", "body", "labels", "milestone", "title"],
            )
        except Exception as exc:
            raise ChildPlanError((f"milestone ensure failed: {exc}",)) from exc

    milestone_number = _milestone_number(parent)
    parent_meta = _safe_metadata(parent_body)
    parent_target_repo = str(parent_meta.get("target_repo") or repo or "").strip()
    plan_rows, has_repo_col = parse_split_plan(
        parent_body, parent_target_repo=parent_target_repo or repo
    )
    if not plan_rows:
        raise ChildPlanError(("split plan not found or empty",))
    if not has_repo_col and not parent_target_repo:
        raise ChildPlanError(("parent YAML missing target_repo",))

    supported = get_config().supported_repos
    labels = _label_names(parent)
    existing_by_title: dict[str, int] = {}
    for item in _list_chain_children(client, parent_number, parent):
        title = str(item.get("title") or "")
        number = item.get("number")
        if title and isinstance(number, int):
            existing_by_title[title] = number

    row_to_issue: dict[int, int] = {}
    for row in plan_rows:
        if row.title in existing_by_title:
            row_to_issue[row.row_num] = existing_by_title[row.title]

    state = Sub1State()
    table_row_count = len(plan_rows)
    row_violations: list[str] = []
    entries: list[ChildPlanEntry] = []

    for row in plan_rows:
        if has_repo_col and not row.repo:
            row_violations.append(f"row {row.row_num} ({row.title}): empty target repository")
            continue
        if row.repo and row.repo not in supported:
            row_violations.append(f"row {row.row_num} ({row.title}): unsupported repo {row.repo!r}")
            continue

        resolved_dep = resolve_dependencies(
            row.dep_raw,
            table_row_count=table_row_count,
            row_to_issue=row_to_issue,
            client=client,
            state=state,
        )
        dep_skip = False
        no_deps = get_config().language.no_deps_word
        if row.dep_raw.strip() and row.dep_raw.strip() != no_deps:
            for match in re.finditer(r"#(\d+)", resolved_dep):
                issue_num = int(match.group(1))
                if issue_num > table_row_count:
                    try:
                        client.issue_get(issue_num, fields=["number"])
                    except Exception:
                        row_violations.append(
                            f"row {row.row_num} ({row.title}): unresolved dependencies"
                        )
                        dep_skip = True
                        break
        if dep_skip:
            continue

        paths = allow_paths_for_row(parent_body, row)
        if not paths:
            row_violations.append(
                f"row {row.row_num} ({row.title}): no allow_paths from change table"
            )
            continue

        draft = build_child_body(
            parent_body=parent_body,
            parent_number=parent_number,
            row=row,
            resolved_dep=resolved_dep,
            client=client,
            parent_labels=labels,
            allow_paths=paths,
        )
        existing = existing_by_title.get(row.title)
        entry = ChildPlanEntry(
            row_number=row.row_num,
            title=row.title,
            repo=row.repo,
            existing=existing,
            dependencies=_issue_numbers_from_resolved(
                resolved_dep,
                row_to_issue=row_to_issue,
                table_row_count=table_row_count,
            ),
            allow_paths=tuple(paths),
            draft_body=draft,
        )
        entries.append(entry)
        if existing is not None:
            row_to_issue[row.row_num] = existing

    if row_violations:
        raise ChildPlanError(tuple(row_violations))

    return ChildPlan(entries=tuple(entries), milestone_number=milestone_number)


def create_child(
    client: ForgePort,
    parent_number: int,
    entry: ChildPlanEntry,
    *,
    body: str | None = None,
) -> int:
    """Create or reuse a child Issue for *entry*; restore deterministic YAML/deps when *body* is set."""
    if entry.existing is not None:
        if not ensure_sub1_binding(client, parent_number, entry.existing):
            raise ChildPlanError((f"sub-issue link failed for existing #{entry.existing}",))
        return entry.existing

    parent = client.issue_get(
        parent_number,
        fields=["number", "body", "labels", "milestone", "title"],
    )
    dep_heading = get_config().sections["dependencies"]
    final_body = (
        entry.draft_body
        if body is None
        else _restore_deterministic_parts(entry.draft_body, body, dep_heading=dep_heading)
    )

    resolved_dep = _resolved_dep_line(final_body)
    supported = get_config().supported_repos
    pre_fail = prevalidate_child_body(
        body=final_body,
        row_repo=entry.repo,
        parent_issue_number=parent_number,
        resolved_dep=resolved_dep,
        client=client,
        supported=supported,
    )
    if pre_fail:
        raise ChildPlanError(tuple(pre_fail))

    milestone_number = _milestone_number(parent)
    labels = _label_names(parent)
    scope_labels = [
        lab for lab in labels if lab.startswith("scope:") and lab != "scope:milestone"
    ]
    create_labels = ["issuesmith:draft-done", *scope_labels]

    try:
        new_number = int(
            client.issue_create(
                entry.title,
                final_body,
                labels=create_labels,
                milestone=milestone_number,
            )
        )
    except Exception as exc:
        raise ChildPlanError((f"issue create failed: {exc}",)) from exc

    if not ensure_sub1_binding(client, parent_number, new_number):
        raise ChildPlanError((f"sub-issue link failed for #{new_number}",))
    return new_number


def _resolved_dep_line(body: str) -> str:
    lang = get_config().language
    dep_col = lang.sub_plan_columns[4]
    dep_line_re = re.compile(rf"^{re.escape(dep_col)}:\s*(.*)$", re.MULTILINE)
    match = dep_line_re.search(body)
    if match:
        return match.group(1).strip()
    return lang.no_deps_word


def validate_children(
    client: ForgePort,
    parent_number: int,
    children: Sequence[dict[str, Any]],
) -> tuple[str, ...]:
    """Run milestone child validation; return violation messages (empty means pass)."""
    parent = client.issue_get(
        parent_number,
        fields=["number", "body", "labels", "milestone", "title"],
    )
    result = _validate_children(parent, list(children), client=client)
    violations: list[str] = []
    for item in result.results:
        for failure in item.failures:
            violations.append(f"#{item.issue}: {failure}")
    return tuple(violations)


def widen_allow_paths(
    client: ForgePort,
    issue_number: int,
    files: Sequence[str],
) -> bool:
    """Append normalized *files* to the leading YAML ``allow_paths`` (idempotent)."""
    issue = client.issue_get(issue_number, fields=["body"])
    body = str(issue.get("body") or "")
    if not body.strip():
        return False

    merged_new: list[str] = []
    seen: set[str] = set()
    for raw in files:
        try:
            path = normalize_allow_path(str(raw))
        except ValueError:
            continue
        if path not in seen:
            seen.add(path)
            merged_new.append(path)

    meta = _safe_metadata(body)
    existing_raw = meta.get("allow_paths") or []
    existing = [str(p) for p in existing_raw if isinstance(p, str)]
    merged = list(existing)
    for path in merged_new:
        if path not in merged:
            merged.append(path)

    if merged == existing:
        return True

    new_body = replace_allow_paths(body, merged)
    if new_body is None:
        return False

    client.issue_update(issue_number, body=new_body)
    return True
