"""body_editor.py — ghdag.markdown.body_editor への互換シム + nexus 側拡張。

実装本体は ghdag 側へ移設済み。brushup.md / sub-ready.md の order テンプレートは
`issuesmith.body_editor` を import する前提で書かれているため、この import パスを維持する。
"""
from __future__ import annotations

from ghdag.markdown.body_editor import (
    count_heading,
    filter_section_by_paths,
    get_section,
    get_subsections,
    split_h2_sections,
    upsert_section,
)

__all__ = [
    "count_heading",
    "filter_section_by_paths",
    "get_section",
    "get_section_by_keyword",
    "get_subsections",
    "split_h2_sections",
    "upsert_section",
]


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
