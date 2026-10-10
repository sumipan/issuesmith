"""Canonical contract types and extractors for issuesmith (#3487, #4272).

Workflow design rule (nexus docs/ISSUESMITH.md, workflow design conventions R1):
every gate that validates a contract section and every step that consumes it
MUST call the same function from this module. Defining a second parser for
the same section anywhere else is a convention violation and is detected by
``tests/test_workflow_conventions.py``.

Sections covered here:

* the changed-files section (change table) — bold-label form
  ``**<changed_files>**:`` used inside ``#### <sub_header_prefix>N:`` blocks and the
  H2/H3 heading form used at Issue top level. Both forms resolve through
  :func:`extract_change_table_rows`. Heading words and table columns come from the
  configured language pack (``get_config().language``).

Step contract types (:class:`StepContext`, :class:`StepResult`, :class:`Andon`,
:class:`Verdict`) are the canonical definitions for dispatch and step runners.
Import them from this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Literal

import yaml

from issuesmith.config import get_config

if TYPE_CHECKING:
    from issuesmith.engine import RetrySignal

_SECTION_END = r"(?=^##(?!#)|\Z)"


@lru_cache(maxsize=8)
def _compile_sub_header(prefix: str) -> re.Pattern[str]:
    return re.compile(rf"^####\s+{re.escape(prefix)}[ \t]*(\d+):", re.MULTILINE)


def sub_header_re() -> re.Pattern[str]:
    """Regex of a sub-design header ``#### <sub_header_prefix>N:`` (group 1 = N).

    The prefix comes from the configured language pack, read at call time.
    """
    return _compile_sub_header(get_config().language.sub_header_prefix)


class _LazySubHeaderRe:
    """``SUB_HEADER_RE`` compatibility object delegating to :func:`sub_header_re`.

    Resolved on every call so importing this module never loads the config.
    """

    def search(self, *args: Any, **kwargs: Any) -> re.Match[str] | None:
        return sub_header_re().search(*args, **kwargs)

    def match(self, *args: Any, **kwargs: Any) -> re.Match[str] | None:
        return sub_header_re().match(*args, **kwargs)

    def finditer(self, *args: Any, **kwargs: Any) -> Any:
        return sub_header_re().finditer(*args, **kwargs)

    def findall(self, *args: Any, **kwargs: Any) -> list[Any]:
        return sub_header_re().findall(*args, **kwargs)

    def sub(self, *args: Any, **kwargs: Any) -> str:
        return sub_header_re().sub(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(sub_header_re(), name)

    def __repr__(self) -> str:
        return "<lazy SUB_HEADER_RE (see contract.sub_header_re)>"


SUB_HEADER_RE = _LazySubHeaderRe()
_H1_H3_RE = re.compile(r"^#{1,3}\s", re.MULTILINE)
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")

# Split-plan depends-on column tokens (#4929): shared by B1 verify and SUB1.
PLAN_DEP_REF_RE = re.compile(r"#?(\d+)")


def plan_dep_refs(cell: str) -> list[int]:
    """Dependency cell refs in order, deduplicated (``#4, 2, #4`` → ``[4, 2]``)."""
    seen: set[int] = set()
    out: list[int] = []
    for match in PLAN_DEP_REF_RE.finditer(cell or ""):
        n = int(match.group(1))
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


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
        "iter_sub_blocks",
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


def _split_table_row(line: str) -> list[str]:
    """Split a ``| a | b |`` row, ignoring ``|`` inside inline-code spans (#3481).

    An unmatched backtick is literal (CommonMark), so it never opens a span.
    """
    cells: list[str] = []
    current: list[str] = []
    in_tick = False
    for i, ch in enumerate(line):
        if ch == "`" and (in_tick or "`" in line[i + 1 :]):
            in_tick = not in_tick
        if ch == "|" and not in_tick:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    if cells and not cells[0]:
        cells = cells[1:]
    if cells and not cells[-1]:
        cells = cells[:-1]
    return cells


def parse_table_rows(section: str) -> list[list[str]]:
    """Parse markdown table rows (header included, separator excluded)."""
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not _TABLE_ROW_RE.match(stripped):
            continue
        if _TABLE_SEP_RE.match(stripped):
            continue
        cells = _split_table_row(stripped)
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
    columns = get_config().language.change_table_columns
    result: list[tuple[str, str, str]] = []
    for region in _change_table_bodies(text):
        rows = parse_table_rows(region)
        if len(rows) <= 1:
            continue
        header = [c.lower() for c in rows[0]]
        repo_col, path_col, type_col = (c.lower() for c in columns[:3])
        try:
            repo_idx = next(i for i, c in enumerate(header) if repo_col in c)
            path_idx = next(i for i, c in enumerate(header) if path_col in c)
            type_idx = next(i for i, c in enumerate(header) if type_col in c)
        except StopIteration:
            # 3-column legacy form without repo column: | path | type | desc |
            if len(rows[0]) >= 2 and path_col not in rows[0][0].lower():
                for row in rows:
                    if len(row) >= 2 and path_col not in row[0].lower():
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
        if path not in paths:
            paths.append(path)
    return paths


def iter_sub_blocks(text: str) -> list[tuple[int, str]]:
    """Return (sub_num, block_text) for every sub_header_re() header in text, in order.

    A block runs from its header to the earliest of: the next sub_header_re() header,
    the next heading of level 1-3 (^#{1,3}\\s), or the end of text.
    ``####`` and deeper headings do not terminate a block.
    """
    headers = list(sub_header_re().finditer(text))
    if not headers:
        return []
    result: list[tuple[int, str]] = []
    for idx, match in enumerate(headers):
        sub_num = int(match.group(1))
        start = match.start()
        next_sub_start = headers[idx + 1].start() if idx + 1 < len(headers) else len(text)
        next_h = _H1_H3_RE.search(text, match.end())
        next_h_start = next_h.start() if next_h else len(text)
        end = min(next_sub_start, next_h_start)
        result.append((sub_num, text[start:end]))
    return result


def parse_frontmatter_fields(body: str) -> dict[str, Any]:
    """Extract leading YAML block fields used by issuesmith."""
    text = body or ""
    # Support both ```yaml ... ``` and --- ... --- (legacy). Prefer ```yaml.
    m = re.match(r"^\s*```ya?ml\s*\n(.*?)\n```", text, re.DOTALL | re.IGNORECASE)
    if m:
        raw = m.group(1)
    else:
        m = re.match(r"^\s*---\s*\n(.*?)\n---", text, re.DOTALL)
        raw = m.group(1) if m else ""
    if not raw.strip():
        return {}
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def validate_frontmatter(body: str) -> list[str]:
    """Return missing YAML contract field names (empty list when valid)."""
    data = parse_frontmatter_fields(body)
    missing: list[str] = []
    if not data.get("target_repo"):
        missing.append("target_repo")
    if not data.get("base_branch"):
        missing.append("base_branch")
    allow = data.get("allow_paths")
    if not isinstance(allow, list) or not allow:
        missing.append("allow_paths")
    return missing


def sub_block(body: str, sub_num: int) -> str:
    """Text of the ``#### <sub_header_prefix><sub_num>:`` block ("" if absent).

    Shared by the B1 gate (per-block checks) and SUB1 (child allow_paths) so both
    look at the same text. Block ends at the next sub header, next H1-H3 heading,
    or end of body — whichever comes first.
    """
    for num, block in iter_sub_blocks(body):
        if num == sub_num:
            return block
    return ""


# ---------------------------------------------------------------------------
# Step contract types (#4272) — canonical definitions for dispatch / runners
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepContext:
    issue_number: str
    base_branch: str
    handler_name: str
    is_cross_repo: str  # "true" | "false"
    target_clone_path: str
    source: str
    workflow_name: str
    m1_result_filename: str
    m1r_result_filename: str
    # context_hook / workflow vars (defaults keep m2_finalize callers compatible)
    worktree_path: str = ""
    target_worktree_path: str = ""
    branch: str = ""
    target_repo: str = ""
    allow_paths: str = ""
    diary_worktree_path: str = ""
    has_diary_changes: str = ""
    pipeline_id: str = ""
    diary_allow_paths: str = ""
    host_worktree_path: str = ""
    has_host_changes: str = ""
    host_allow_paths: str = ""
    issue_repo: str = ""
    p1_result_filename: str = ""
    p2_result_filename: str = ""
    p3_result_filename: str = ""
    execution_constraints: str = ""
    repair_violations: str = ""
    repair_step_origin: str = ""

    def __post_init__(self) -> None:
        for new_attr, old_attr in (
            ("host_worktree_path", "diary_worktree_path"),
            ("has_host_changes", "has_diary_changes"),
            ("host_allow_paths", "diary_allow_paths"),
        ):
            new_val = getattr(self, new_attr)
            old_val = getattr(self, old_attr)
            if new_val and not old_val:
                object.__setattr__(self, old_attr, new_val)
            elif old_val and not new_val:
                object.__setattr__(self, new_attr, old_val)


@dataclass
class Andon:
    """Lightweight Andon spec returned from step implementations.

    Dispatch constructs the full issuesmith.andon.Andon from context fields.
    """
    kind: str  # "decision" | "blocked" | "broken"
    summary: str = ""
    rule_id: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    """Gate verdict for irreversible step pre-checks."""
    passed: bool
    reason: str = ""


@dataclass
class StepResult:
    # New primary contract (3-value status)
    status: Literal["done", "retry", "andon"] = "done"
    markers: list[str] = field(default_factory=list)
    retry: RetrySignal | None = None
    andon: Andon | None = None
    artifacts: dict = field(default_factory=dict)
    irreversible: bool = False

    # 1-release compat: old-style fields (removed next release)
    exit_code: int | None = None
    pipeline_status: str | None = None
    recovery: str | None = None

    def __post_init__(self) -> None:
        # Compat: old exit_code=0 / pipeline_status → new markers
        if self.exit_code == 0 and self.pipeline_status and not self.markers:
            self.markers = [self.pipeline_status]


# ---------------------------------------------------------------------------
# Label vocabulary (#4807) — suffixes after ``<label_namespace>:``
# ---------------------------------------------------------------------------

# Phase-axis statuses, least to most advanced (``<phase>-<status>``).
PHASE_STATUSES: tuple[str, ...] = ("ready", "running", "done")
QUEUED = "queued"
WAITING = "waiting"
ANDON_PREFIX = "andon-"
# Andon kinds, most urgent first: the attention axis shows only the first one present.
ANDON_KINDS: tuple[str, ...] = ("broken", "decision", "blocked")


class LabelWriteForbidden(RuntimeError):
    """A step wrote labels through the forge while ``label_write_guard`` is ``enforce``."""

    def __init__(self, step_id: str, labels: list[str]) -> None:
        self.step_id = step_id
        self.labels = list(labels)
        super().__init__(
            f"step {step_id} wrote labels {self.labels}; labels are projected by the runner"
        )
