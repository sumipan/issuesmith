"""body_editor.py — compatibility shim for ghdag.markdown.body_editor plus host extensions.

The implementation moved to ghdag. The brushup.md / sub-ready.md order templates
import ``issuesmith.body_editor``, so this import path is kept.
"""
from __future__ import annotations

import re

import yaml
from ghdag.markdown.body_editor import (
    count_heading,
    filter_section_by_paths,
    get_section,
    get_subsections,
    split_h2_sections,
    upsert_section,
)

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

    ghdag's get_section matches H2 headings exactly and misses suffixed headings
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
