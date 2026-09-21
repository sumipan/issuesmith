"""body_editor.py — ghdag.markdown.body_editor への互換シム + nexus 側拡張。

実装本体は ghdag 側へ移設済み。brushup.md / sub-ready.md の order テンプレートは
`issuesmith.body_editor` を import する前提で書かれているため、この import パスを維持する。
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
_OUT_OF_SCOPE_HEADING = "やらないこと"


def get_section_by_keyword(body: str, keyword: str) -> str | None:
    """見出しに keyword を **含む** 最初の H2 セクションの本文を返す。

    ghdag の get_section は H2 完全一致のみで、B1 が書く
    `## 受け入れ条件（Phase 1）` のようなサフィックス付き見出しを取り落とす
    （#2535 の SUB1 偽陰性）。ac_contract の抽出規約と同じ「部分一致 H2」で
    解決するゲート用フォールバック。見つからなければ None。
    """
    for heading, content in split_h2_sections(body):
        if keyword in heading:
            return content
    return None


def normalize_sub_headers(body: str) -> str:
    """``#### Sub N:`` / ``#### sub N`` を ``#### サブN:`` に正規化する。"""
    return _SUB_HEADER_EN_RE.sub(r"\g<1>サブ\g<2>:", body)


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
    """``## 設計`` 配下の ``### サブイシュー分割計画`` を ``## マイルストーン`` へ移す。

    前提を満たさない場合は入力をそのまま返す。
    """
    sections = get_config().sections
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
        # Insert ## マイルストーン before ## やらないこと, else append.
        h2_out = f"## {_OUT_OF_SCOPE_HEADING}"
        milestone_section = f"## {milestone_name}\n{plan_content}"
        if h2_out in body_without_plan:
            return body_without_plan.replace(h2_out, f"{milestone_section}\n\n{h2_out}", 1)
        trimmed = body_without_plan.rstrip()
        return f"{trimmed}\n\n{milestone_section}"

    existing = get_section(body_without_plan, milestone_name) or ""
    merged = f"{existing.rstrip()}\n\n{plan_content}".strip("\n")
    return upsert_section(body_without_plan, milestone_name, merged)
