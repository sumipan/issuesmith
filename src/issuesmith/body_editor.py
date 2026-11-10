"""Utilities for deterministically editing H2 sections of an Issue body."""
from __future__ import annotations

import re
from collections.abc import Iterable

import yaml

from issuesmith.config import get_config

__all__ = [
    "count_heading",
    "filter_section_by_paths",
    "get_section",
    "get_section_by_keyword",
    "get_subsections",
    "normalize_sub_headers",
    "relocate_sub_plan",
    "apply_milestone_normalizers",
    "replace_allow_paths",
    "split_h2_sections",
    "upsert_section",
]


def split_h2_sections(body: str) -> list[tuple[str, str]]:
    """Split body into H2-heading units."""
    lines = body.splitlines()
    sections: list[tuple[str, str]] = []
    n = len(lines)
    i = 0

    while i < n:
        line = lines[i]
        if line.startswith("## ") and not line.startswith("###"):
            heading = line
            start = i
            i += 1
            while i < n:
                nxt = lines[i]
                if nxt.startswith("## ") and not nxt.startswith("###"):
                    break
                i += 1
            section = "\n".join(lines[start:i])
            if i < n or body.endswith("\n"):
                section += "\n"
            sections.append((heading, section))
        else:
            i += 1

    return sections


def count_heading(body: str, heading: str) -> int:
    """Return the number of occurrences of the given H2 heading."""
    h2 = f"## {heading}"
    return sum(1 for section_heading, _ in split_h2_sections(body) if section_heading == h2)


def _extract_section_body(section: str) -> str:
    parts = section.split("\n", 1)
    if len(parts) == 1:
        return ""
    return parts[1]


def _get_unique_section(body: str, heading: str) -> str | None:
    h2 = f"## {heading}"
    matched = [
        section for section_heading, section in split_h2_sections(body) if section_heading == h2
    ]
    if len(matched) > 1:
        raise ValueError(f"Duplicate heading: '{h2}' appears {len(matched)} times")
    return matched[0] if matched else None


def get_section(body: str, heading: str) -> str | None:
    """Return the section body of the given H2 heading (excluding the heading line)."""
    section = _get_unique_section(body, heading)
    if section is None:
        return None
    return _extract_section_body(section)


def _normalize_content(content: str) -> str:
    return content if content.endswith("\n") else f"{content}\n"


def upsert_section(body: str, heading: str, content: str) -> str:
    """Replace the section or append it at the end; reject duplicate headings."""
    h2 = f"## {heading}"
    target = _get_unique_section(body, heading)
    normalized_content = _normalize_content(content)
    new_section = f"{h2}\n{normalized_content}"

    if target is not None:
        sections = split_h2_sections(body)
        rebuilt: list[str] = []
        for section_heading, section in sections:
            if section_heading == h2:
                rebuilt.append(new_section.rstrip("\n"))
            else:
                rebuilt.append(section.rstrip("\n"))
        return "\n".join(rebuilt)

    trimmed = body.rstrip()
    if not trimmed:
        return new_section.rstrip("\n")
    return f"{trimmed}\n\n{new_section.rstrip()}"


def _iter_h4_sections(content: str) -> Iterable[tuple[str, str]]:
    lines = content.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if line.startswith("#### "):
            heading = line
            start = i + 1
            i += 1
            while i < n and not lines[i].startswith("#### "):
                i += 1
            body_part = "\n".join(lines[start:i])
            if body_part:
                body_part += "\n"
            yield heading, body_part
        else:
            i += 1


def filter_section_by_paths(section_content: str, sub_paths: list[str]) -> list[str]:
    """Line-filtering algorithm (shared by R3 and R4)."""
    lines = section_content.splitlines()
    groups: list[tuple[str | None, list[str]]] = []
    cur_h3: str | None = None
    cur_lines: list[str] = []

    for line in lines:
        if line.startswith("### "):
            groups.append((cur_h3, cur_lines))
            cur_h3 = line
            cur_lines = []
        else:
            cur_lines.append(line)
    groups.append((cur_h3, cur_lines))

    result: list[str] = []
    for h3, glines in groups:
        filtered = [
            line
            for line in glines
            if not line.startswith("- ")
            or any(p in line or p.rsplit("/", 1)[-1] in line for p in sub_paths)
        ]
        has_list = any(line.startswith("- ") for line in filtered)
        if h3 is None:
            result.extend(filtered)
        elif has_list:
            result.append(h3)
            result.extend(filtered)
        else:
            result.extend(line for line in filtered if line.strip())

    if not any(line.startswith("- ") for line in result):
        return []

    return result


def get_subsections(body: str, parent_heading: str, prefix: str) -> list[tuple[str, str]]:
    """Return H4 subsections matching prefix within the parent H2 section."""
    parent_content = get_section(body, parent_heading)
    if parent_content is None:
        return []

    return [
        (heading, subsection_content)
        for heading, subsection_content in _iter_h4_sections(parent_content)
        if heading.startswith(prefix)
    ]


_LEADING_YAML_BLOCK_RE = re.compile(r"^```yaml\n(.*?)\n```", re.DOTALL | re.MULTILINE)


def replace_allow_paths(body: str, new_paths: list[str]) -> str | None:
    """Rewrite ``allow_paths`` inside the leading ```yaml metadata block (#3487).

    Regenerates the whole block from the parsed mapping (``yaml.safe_dump``)
    rather than splicing lines, so quoting/list-style differences in the
    original never produce a malformed block. ``None`` when the body has no
    leading yaml block or that block has no ``allow_paths`` key — callers must
    not persist a body in that case (nothing to safely replace).
    """
    match = _LEADING_YAML_BLOCK_RE.search(body)
    if not match:
        return None
    data = yaml.safe_load(match.group(1))
    if not isinstance(data, dict) or "allow_paths" not in data:
        return None
    data["allow_paths"] = list(new_paths)
    new_raw = yaml.safe_dump(
        data, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")
    return body[: match.start(1)] + new_raw + body[match.end(1) :]

_SUB_HEADER_EN_RE = re.compile(
    r"^(####\s+)Sub[ \t]+(\d+)[ \t]*:?",
    re.MULTILINE | re.IGNORECASE,
)


def get_section_by_keyword(body: str, keyword: str) -> str | None:
    """Return the body of the first H2 section whose heading **contains** ``keyword``.

    get_section matches H2 headings exactly and misses suffixed headings
    B1 writes, such as ``## <acceptance criteria> (Phase 1)`` (the SUB1 false
    negative in #2535). Gate fallback using the same "partial H2 match" rule as
    the ac_contract extractor. ``None`` when not found.
    """
    for heading, content in split_h2_sections(body):
        if keyword in heading:
            return content
    return None


def normalize_sub_headers(body: str) -> str:
    """Normalize ``#### Sub N:`` / ``#### sub N`` to ``#### <sub_header_prefix>N:``.

    The canonical prefix comes from the configured language pack.
    """
    prefix = get_config().language.sub_header_prefix
    return _SUB_HEADER_EN_RE.sub(lambda m: f"{m.group(1)}{prefix}{m.group(2)}:", body)


def _extract_h3_subsection(section: str, heading: str) -> tuple[str, str] | None:
    """Return (subsection_including_heading, section_without_it) or None."""
    pattern = re.compile(
        rf"(^###\s+{re.escape(heading)}\s*\n.*?)(?=^###\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(section)
    if not match:
        return None
    extracted = match.group(1)
    remainder = section[: match.start()] + section[match.end() :]
    return extracted, remainder


def relocate_sub_plan(body: str) -> str:
    """Move ``### <sub_plan>`` from under ``## <design>`` to ``## <milestone>``.

    Returns the input unchanged when the preconditions do not hold.
    """
    cfg = get_config()
    sections = cfg.sections
    design_name = sections["design"]
    milestone_name = sections["milestone"]
    plan_name = sections["sub_plan"]

    design = get_section(body, design_name)
    if design is None:
        return body
    extracted = _extract_h3_subsection(design, plan_name)
    if extracted is None:
        return body
    plan_block, design_remainder = extracted

    milestone = get_section(body, milestone_name)
    if milestone is not None and re.search(
        rf"^###\s+{re.escape(plan_name)}\s*$", milestone, re.MULTILINE
    ):
        return body

    body_without_plan = upsert_section(body, design_name, design_remainder.strip("\n"))
    plan_content = plan_block.strip("\n")

    if get_section(body_without_plan, milestone_name) is None:
        # Insert ## <milestone> before ## <out of scope>, else append.
        h2_out = f"## {cfg.language.out_of_scope_heading}"
        milestone_section = f"## {milestone_name}\n{plan_content}"
        if h2_out in body_without_plan:
            return body_without_plan.replace(h2_out, f"{milestone_section}\n\n{h2_out}", 1)
        trimmed = body_without_plan.rstrip()
        return f"{trimmed}\n\n{milestone_section}"

    existing = get_section(body_without_plan, milestone_name) or ""
    merged = f"{existing.rstrip()}\n\n{plan_content}".strip("\n")
    return upsert_section(body_without_plan, milestone_name, merged)


def apply_milestone_normalizers(body: str) -> str:
    """Apply ``normalize_sub_headers`` then ``relocate_sub_plan`` (idempotent)."""
    return relocate_sub_plan(normalize_sub_headers(body))
