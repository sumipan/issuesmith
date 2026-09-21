"""Canonical extractors for Issue-body contract sections (#3487).

Workflow design rule (nexus docs/ISSUESMITH.md "ワークフロー設計規約" R1):
every gate that validates a contract section and every step that consumes it
MUST call the same function from this module. Defining a second parser for
the same section anywhere else is a convention violation and is detected by
``tests/test_workflow_conventions.py``.

Sections covered here:

* ``変更対象ファイル`` (change table) — bold-label form ``**変更対象ファイル**:``
  used inside ``#### サブN`` blocks and the H2/H3 heading form used at Issue top
  level. Both forms resolve through :func:`extract_change_table_rows`.
"""

from __future__ import annotations

import re

from issuesmith.config import get_config

_SECTION_END = r"(?=^##(?!#)|\Z)"
SUB_HEADER_RE = re.compile(r"^####\s+サブ(\d+):", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")

# Canonical extractor names. A ``def`` with one of these names outside this
# module is a duplicate parser (R1). Keep in sync with tests/test_workflow_conventions.py.
CONTRACT_EXTRACTORS: frozenset[str] = frozenset(
    {
        "get_section",
        "parse_table_rows",
        "_parse_table_rows",
        "extract_change_table_rows",
        "_extract_paths_from_table_section",
        "_extract_paths_from_change_table",
        "_extract_change_paths",
        "change_paths_for_repo",
        "_change_paths_for_repo",
    }
)


def get_section(body: str, heading: str) -> str | None:
    """Return the body of the H2 section ``## <heading>`` (heading line excluded)."""
    match = re.search(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?){_SECTION_END}",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def parse_table_rows(section: str) -> list[list[str]]:
    """Parse markdown table rows (header included, separator excluded)."""
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not _TABLE_ROW_RE.match(stripped):
            continue
        if _TABLE_SEP_RE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


def _normalize_path(path: str) -> str:
    path = path.strip().strip("`").strip()
    # "src/x.py (new)" style annotations
    return re.sub(r"\([^)]*\)", "", path).strip()


def _change_table_bodies(text: str) -> list[str]:
    """Return every change-table candidate region in ``text``.

    Recognised markers (all resolve to the same table):
    * ``**<changed_files>**:`` bold label (sub-design blocks)
    * ``## <changed_files>`` / ``### <changed_files>`` headings
    When no marker is present the whole text is treated as one candidate so a
    bare table block still parses.
    """
    changed = re.escape(get_config().sections["changed_files"])
    marker = re.compile(
        rf"(?:\*\*{changed}\*\*:?|^#{{2,4}}\s+{changed})\s*\n",
        re.MULTILINE,
    )
    regions: list[str] = []
    matches = list(marker.finditer(text))
    if not matches:
        return [text]
    for idx, match in enumerate(matches):
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        region = text[start:end]
        # stop at the next bold label / heading inside the region
        cut = re.search(r"^(?:\*\*[^*\n]+\*\*:?\s*$|#{1,4}\s)", region, re.MULTILINE)
        if cut:
            region = region[: cut.start()]
        regions.append(region)
    return regions


def extract_change_table_rows(text: str) -> list[tuple[str, str, str]]:
    """Return ``(repo, path, change_type)`` for every row of every change table in ``text``.

    ``repo`` is ``""`` when the table has no repository column.
    """
    result: list[tuple[str, str, str]] = []
    for region in _change_table_bodies(text):
        rows = parse_table_rows(region)
        if len(rows) <= 1:
            continue
        header = [c.lower() for c in rows[0]]
        try:
            repo_idx = next(i for i, c in enumerate(header) if "リポジトリ" in c)
            path_idx = next(
                i for i, c in enumerate(header) if "ファイルパス" in c or "パス" in c
            )
            type_idx = next(
                i for i, c in enumerate(header) if "変更種別" in c or "種別" in c
            )
        except StopIteration:
            # 3-column legacy form without repo column: | path | type | desc |
            if len(rows[0]) >= 2 and "ファイル" not in rows[0][0]:
                for row in rows:
                    if len(row) >= 2 and "ファイル" not in row[0]:
                        path = _normalize_path(row[0] if "`" in row[0] else row[1])
                        change_type = (
                            row[1] if "`" in row[0] else (row[2] if len(row) > 2 else "")
                        )
                        if path and "/" in path:
                            result.append(("", path, change_type))
            continue
        for row in rows[1:]:
            if len(row) <= max(repo_idx, path_idx, type_idx):
                continue
            repo = row[repo_idx].strip().strip("`")
            path = _normalize_path(row[path_idx])
            change_type = row[type_idx].strip()
            if path:
                result.append((repo, path, change_type))
    return result


def change_paths_for_repo(text: str, repo: str | None = None) -> list[str]:
    """Paths from the change table(s) in ``text``.

    With ``repo`` only rows whose repository cell equals ``repo`` are kept;
    rows without a repository column are kept for any ``repo``.
    Order preserved, duplicates removed. Empty list means the contract section
    is unreadable for that repo — callers MUST fail, never substitute.
    """
    paths: list[str] = []
    for row_repo, path, _ in extract_change_table_rows(text):
        if repo is not None and row_repo and row_repo != repo:
            continue
        if "/" not in path:
            continue
        if path not in paths:
            paths.append(path)
    return paths


def sub_block(body: str, sub_num: int) -> str:
    """Text of ``#### サブ<sub_num>:`` up to the next サブ header or H2 heading ("" if absent).

    Shared by the B1 gate (per-block checks) and SUB1 (child allow_paths) so both
    look at the same text.
    """
    headers = list(SUB_HEADER_RE.finditer(body))
    for idx, match in enumerate(headers):
        if int(match.group(1)) != sub_num:
            continue
        start = match.start()
        next_sub = headers[idx + 1].start() if idx + 1 < len(headers) else len(body)
        next_h2 = re.compile(r"^##(?!#)", re.MULTILINE).search(body, match.end())
        end = min(next_sub, next_h2.start()) if next_h2 else next_sub
        return body[start:end]
    return ""
