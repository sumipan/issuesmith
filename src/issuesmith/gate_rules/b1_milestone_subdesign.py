from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from ghdag.workflow.gates import GATE_REGISTRY, Violation

from issuesmith.config import get_config
from issuesmith.context_hook import parse_issue_metadata, parse_issue_metadata_blocks
from issuesmith.contract import (  # noqa: F401 — re-exported for legacy importers
    SUB_HEADER_RE,
    _normalize_path,
    change_paths_for_repo,
    extract_change_table_rows,
    get_section,
    iter_sub_blocks,
    parse_table_rows,
    plan_dep_refs,
)
from issuesmith.targets import companion_allow_paths_raw

_SUB_HEADER_RE = SUB_HEADER_RE
_BACKTICK_PATH_RE = re.compile(r"`([^`]+)`")
_FILE_REF_RE = re.compile(r"`([^`]+\.[a-zA-Z0-9]+)`|(?:^|[\s(/])([\w./-]+\.[a-zA-Z0-9]+)")

# Sub design text announcing that the sub fails the tests on its own (#4518).
# src/ must stay CJK-free (tests/test_no_cjk_src.py) and the language pack has no field for
# these phrases yet, so only the EN wording is matched.
_STANDALONE_FAIL_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:this\s+sub|sub\s*\d+)\s+(?:alone|on\s+its\s+own|standalone)\b"
        r"[^\n.]*?(?:ImportError|\btests?\b|\bcollect)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:full|whole|entire)\s+test\s+(?:suite\s+)?pass\b[^\n.]*\bparent\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btests?\s+(?:are\s+|is\s+)?(?:followed|updated|fixed|handled)\s+(?:up\s+)?"
        r"(?:in|by)\s+(?:another|a\s+sibling|a\s+separate|a\s+different)\s+sub\b",
        re.IGNORECASE,
    ),
)
# Acceptance criteria wording that requires the whole test suite to pass.
_FULL_SUITE_AC_RE = re.compile(
    r"\b(?:existing\s+tests|all\s+tests|full\s+test\s+suite|tests?\s+pass|collection)\b",
    re.IGNORECASE,
)


def _sub_location(sub_num: int) -> str:
    """``#### <sub_header_prefix>N`` location of a sub design block."""
    return f"#### {get_config().language.sub_header_prefix}{sub_num}"


def extract_sub_blocks(body: str) -> list[tuple[int, str]]:
    design = get_section(body, get_config().sections["design"])
    return iter_sub_blocks(design or "")


def _count_sub_plan_rows(body: str) -> int | None:
    sections = get_config().sections
    milestone = get_section(body, sections["milestone"])
    if not milestone:
        return None
    plan = sections["sub_plan"]
    plan_match = re.search(
        rf"###\s+{re.escape(plan)}\s*\n(.*?)(?=^###|\Z)",
        milestone,
        re.MULTILINE | re.DOTALL,
    )
    if not plan_match:
        return None
    rows = parse_table_rows(plan_match.group(1))
    if len(rows) <= 1:
        return 0
    data_rows = 0
    for row in rows[1:]:
        if row and row[0].strip().isdigit():
            data_rows += 1
    return data_rows


# Canonical extractors live in issuesmith.contract (R1). Aliases keep old names importable.
_extract_paths_from_table_section = extract_change_table_rows
_extract_paths_from_change_table = extract_change_table_rows


def _extract_parent_change_paths(body: str) -> set[str]:
    section = get_section(body, get_config().sections["changed_files"])
    if not section:
        return set()
    # Same extraction as _check_change_paths_readable / SUB1 (#4853).
    return set(change_paths_for_repo(section))


def _allowed_repos(body: str) -> set[str]:
    """Union of every metadata block's target_repo (+ host companion) — one block per repo (#4076)."""
    repos: set[str] = set()
    for metadata in parse_issue_metadata_blocks(body):
        target_repo = metadata.get("target_repo")
        if isinstance(target_repo, str) and target_repo.strip():
            repos.add(target_repo.strip())
        if companion_allow_paths_raw(metadata):
            repos.add(get_config().repo)
    return repos


def _allow_paths_union(body: str) -> set[str]:
    paths: set[str] = set()
    for metadata in parse_issue_metadata_blocks(body):
        raw = metadata.get("allow_paths") or []
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            continue
        paths.update(p.strip() for p in raw if isinstance(p, str) and p.strip())
    return paths


def _sub_plan_dependencies(body: str) -> dict[int, set[int]]:
    """``{row: {depended rows}}`` from the sub-plan table's depends-on column (#4745)."""
    milestone = get_section(body, get_config().sections["milestone"])
    if not milestone:
        return {}
    plan_match = re.search(
        rf"###\s+{re.escape(get_config().sections['sub_plan'])}\s*\n(.*?)(?=^###|\Z)",
        milestone,
        re.MULTILINE | re.DOTALL,
    )
    if not plan_match:
        return {}
    rows = parse_table_rows(plan_match.group(1))
    if len(rows) <= 1:
        return {}
    dep_column = get_config().language.sub_plan_columns[4]
    dep_i = next((i for i, cell in enumerate(rows[0]) if dep_column in cell), None)
    if dep_i is None:
        return {}
    deps: dict[int, set[int]] = {}
    for row in rows[1:]:
        if len(row) <= dep_i or not row[0].strip().isdigit():
            continue
        deps[int(row[0].strip())] = set(plan_dep_refs(row[dep_i]))
    return deps


def _normalize_stem(stem: str) -> str:
    """Fold case and treat ``-`` / ``_`` as one separator for stem matching (#4745)."""
    return stem.lower().replace("-", "_")


def _mirror_test_dirs(path: str) -> list[str]:
    """Mirror directories under ``tests/`` for a non-test file path (#4912)."""
    parent = Path(path).parent.as_posix()
    if parent == ".":
        parent = ""
    dirs: list[str] = []
    seen: set[str] = set()

    def add(directory: str) -> None:
        if directory not in seen:
            seen.add(directory)
            dirs.append(directory)

    if parent:
        add(f"tests/{parent}")
    else:
        add("tests")
    if parent.startswith("src/"):
        rest = parent[4:]
        if rest:
            add(f"tests/{rest}")
        else:
            add("tests")
        parts = parent.split("/")
        if len(parts) >= 3 and parts[0] == "src":
            tail = "/".join(parts[2:])
            if tail:
                add(f"tests/{tail}")
            else:
                add("tests")
    return dirs


def _has_glob(path: str) -> bool:
    return any(ch in path for ch in "*?[")


def _is_deletion_row(change_type: str, content: str) -> bool:
    from issuesmith.gate_rules import scope_coupling

    lowered = change_type.lower()
    if any(word in lowered for word in scope_coupling._delete_move_keywords()):
        return True
    content_lower = content.lower()
    return any(word in content_lower for word in get_config().language.delete_words)


def _sibling_test_matches_non_deletion(path: str, test_path: str) -> bool:
    if _has_glob(test_path):
        stem_name = Path(path).stem
        for directory in _mirror_test_dirs(path):
            candidate = f"{directory}/test_{stem_name}.py"
            if fnmatch.fnmatchcase(
                _normalize_stem(candidate), _normalize_stem(test_path)
            ):
                return True
        return False
    stem = _normalize_stem(Path(path).stem)
    stem_re = re.compile(rf"(?<![a-z0-9]){re.escape(stem)}(?![a-z0-9])")
    return bool(stem_re.search(_normalize_stem(Path(test_path).stem)))


def _sibling_test_matches(path: str, test_path: str, *, deletion: bool) -> bool:
    if deletion:
        for directory in _mirror_test_dirs(path):
            if directory == "tests":
                continue
            if test_path.startswith(f"{directory}/"):
                return True
        return _sibling_test_matches_non_deletion(path, test_path)
    return _sibling_test_matches_non_deletion(path, test_path)


def _extract_ac_items(section: str) -> list[str]:
    ac = get_config().sections["acceptance_criteria"]
    ac_match = re.search(
        rf"\*\*{re.escape(ac)}\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
        section,
        re.DOTALL,
    )
    if not ac_match:
        return []
    ac_text = ac_match.group(1)
    return re.findall(r"^\s*-\s+\[[ xX]\]\s+(.*)$", ac_text, re.MULTILINE)


def _extract_file_refs_from_text(text: str) -> set[str]:
    refs: set[str] = set()
    for match in _FILE_REF_RE.finditer(text):
        path = match.group(1) or match.group(2)
        if path and not path.startswith("http"):
            refs.add(_normalize_path(path))
    return refs


_DEPENDENCY_MISSING_ID = "b1_milestone_subdesign.sibling_new_file_unreferenced_dependency"
_DEPENDENCY_CYCLE_ID = "b1_milestone_subdesign.dependency_cycle"

PLACEHOLDER_DESIGN_MARKER = "Split work for concern"
PLACEHOLDER_CHANGE_CONTENT = "split from oversized issue"
PLACEHOLDER_DESIGN_ID = "b1_milestone_subdesign.placeholder_design"
PLACEHOLDER_AC_TEMPLATES: tuple[str, ...] = (
    "Concern `{concern}` change table lists every assigned path",
    "Sub design `{num}` includes the required subsections",
    "Parent change-file union includes every path under `{concern}`",
)


def _placeholder_ac_regexes() -> tuple[re.Pattern[str], ...]:
    patterns: list[re.Pattern[str]] = []
    for tpl in PLACEHOLDER_AC_TEMPLATES:
        regex_parts: list[str] = []
        rest = tpl
        while rest:
            if rest.startswith("{concern}"):
                regex_parts.append(".+?")
                rest = rest[len("{concern}") :]
            elif rest.startswith("{num}"):
                regex_parts.append(".+?")
                rest = rest[len("{num}") :]
            else:
                next_at = len(rest)
                for marker in ("{concern}", "{num}"):
                    pos = rest.find(marker)
                    if pos != -1:
                        next_at = min(next_at, pos)
                regex_parts.append(re.escape(rest[:next_at]))
                rest = rest[next_at:]
        patterns.append(re.compile("^" + "".join(regex_parts) + "$"))
    return tuple(patterns)


_PLACEHOLDER_AC_REGEXES = _placeholder_ac_regexes()


def _ac_items_are_template_only(items: list[str]) -> bool:
    if not items:
        return False
    return all(
        any(pattern.fullmatch(item.strip()) for pattern in _PLACEHOLDER_AC_REGEXES)
        for item in items
    )


def _subsection_body_starts_with_marker(text: str) -> bool:
    body = text.strip()
    if body.startswith(":"):
        body = body[1:].lstrip()
    return body.startswith(PLACEHOLDER_DESIGN_MARKER)
_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://\S+")
_FENCE_LANG_RE = re.compile(r"^(\s*(?:```|~~~))[^\s`]*", re.MULTILINE)


def change_rows_with_content(text: str) -> list[tuple[str, str, str, str]]:
    """``(repo, path, change_type, content)`` for every change-table row in ``text``.

    Rows and order match :func:`extract_change_table_rows`; ``content`` is the
    4th (description) column, ``""`` when the table has none (#4825).
    """
    columns = [c.lower() for c in get_config().language.change_table_columns]
    content: dict[tuple[str, str], str] = {}
    for table in re.findall(r"(?:^[ \t]*\|.*(?:\n|\Z))+", text, re.MULTILINE):
        rows = parse_table_rows(table)
        if len(rows) <= 1:
            continue
        header = [c.lower() for c in rows[0]]
        idx = [next((i for i, c in enumerate(header) if col in c), None) for col in columns]
        if any(i is None for i in idx):
            continue
        repo_i, path_i, _, content_i = idx
        for row in rows[1:]:
            if len(row) <= max(idx):
                continue
            key = (row[repo_i].strip().strip("`"), _normalize_path(row[path_i]))
            content.setdefault(key, row[content_i].strip())
    return [
        (repo, path, kind, content.get((repo, path), ""))
        for repo, path, kind in extract_change_table_rows(text)
    ]


def _subsection_text(block: str, name: str) -> str:
    """Text of the ``**name**`` subsection up to the next sub-design subsection label."""
    names = "|".join(re.escape(n) for n in get_config().sub_design_subsections)
    match = re.search(
        rf"\*\*{re.escape(name)}\*\*(.*?)(?=\*\*(?:{names})\*\*|\Z)",
        block,
        re.DOTALL,
    )
    return match.group(1) if match else ""


def _clean_reference_text(text: str, own_paths: list[str]) -> str:
    """Drop URLs, code-fence language names and the consumer's own paths."""
    text = _URL_RE.sub(" ", text)
    text = _FENCE_LANG_RE.sub(r"\1", text)
    for path in sorted(own_paths, key=len, reverse=True):
        text = text.replace(path, " ")
    return text


def _reference_patterns(path: str) -> list[re.Pattern[str]]:
    """Full path / basename (string boundary) and stem (identifier boundary) matchers."""
    name = Path(path).name
    stem = Path(path).stem
    tail = r"(?![\w/-]|\.\w)"
    patterns = [
        re.compile(rf"(?<![\w./-]){re.escape(path)}{tail}"),
        re.compile(rf"(?<![\w.-]){re.escape(name)}{tail}"),
    ]
    if stem:
        patterns.append(re.compile(rf"(?<![A-Za-z0-9_]){re.escape(stem)}(?![A-Za-z0-9_])"))
    return patterns


def _is_new_kind(change_type: str) -> bool:
    lowered = change_type.lower()
    return any(word in lowered for word in get_config().scope_size.new_words)


def _infer_dependency_evidence(
    sub_blocks: list[tuple[int, str]],
) -> dict[int, dict[int, list[str]]]:
    """``{consumer: {creator: [referenced new files]}}`` (#4825)."""
    cfg = get_config()
    _, policy_name, _, ac_name = cfg.sub_design_subsections
    rows = {num: change_rows_with_content(block) for num, block in sub_blocks}
    created = {
        num: [(repo, path) for repo, path, kind, _ in sub_rows if _is_new_kind(kind)]
        for num, sub_rows in rows.items()
    }
    result: dict[int, dict[int, list[str]]] = {num: {} for num, _ in sub_blocks}
    for consumer, block in sub_blocks:
        own = rows[consumer]
        own_keys = {(repo, path) for repo, path, _, _ in own}
        own_paths = [path for _, path, _, _ in own]
        consumer_repos = {repo for repo, _, _, _ in own} or {""}
        prose = _clean_reference_text(
            _subsection_text(block, policy_name) + "\n" + _subsection_text(block, ac_name),
            own_paths,
        )
        segments = [(repo, prose) for repo in sorted(consumer_repos)]
        segments += [
            (repo, _clean_reference_text(content, own_paths))
            for repo, _, _, content in own
            if content
        ]
        for creator, files in created.items():
            if creator == consumer:
                continue
            for repo, path in files:
                if (repo, path) in own_keys:
                    continue
                patterns = _reference_patterns(path)
                if any(
                    (seg_repo == repo or not seg_repo or not repo)
                    and any(p.search(text) for p in patterns)
                    for seg_repo, text in segments
                ):
                    found = result[consumer].setdefault(creator, [])
                    if path not in found:
                        found.append(path)
    return result


def infer_sub_dependencies(
    sub_blocks: list[tuple[int, str]],
) -> dict[int, set[int]]:
    """consumer sub number -> creator sub numbers.

    A consumer depends on a creator when its design policy, change-content cells
    or acceptance criteria reference a file the creator's change table adds (#4825).
    """
    return {
        consumer: set(creators)
        for consumer, creators in _infer_dependency_evidence(sub_blocks).items()
    }


def find_dependency_cycles(graph: dict[int, set[int]]) -> list[list[int]]:
    """Strongly connected components with a cycle (size >= 2 or a self loop), sorted."""
    nodes = sorted(set(graph) | {n for targets in graph.values() for n in targets})
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    components: list[list[int]] = []

    def visit(node: int) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for nxt in sorted(graph.get(node, ())):
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            component: list[int] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in graph.get(node, ()):
                components.append(sorted(component))

    for node in nodes:
        if node not in index:
            visit(node)
    return sorted(components)


def _cycle_path(graph: dict[int, set[int]], component: list[int]) -> list[int]:
    """Deterministic closed walk ``[start, ..., start]`` inside ``component``."""
    start = component[0]
    members = set(component)
    path = [start]
    seen = {start}

    def walk(node: int) -> bool:
        for nxt in sorted(graph.get(node, set()) & members):
            if nxt == start:
                path.append(start)
                return True
            if nxt not in seen:
                seen.add(nxt)
                path.append(nxt)
                if walk(nxt):
                    return True
                path.pop()
        return False

    walk(start)
    return path


def _dependency_graph(
    declared: dict[int, set[int]], inferred: dict[int, set[int]]
) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = {}
    for deps in (declared, inferred):
        for consumer, creators in deps.items():
            graph.setdefault(consumer, set()).update(creators)
    return graph


def _missing_dependencies(body: str) -> dict[int, dict[int, list[str]]]:
    """Inferred edges absent from the sub plan, excluding edges inside a cycle."""
    evidence = _infer_dependency_evidence(extract_sub_blocks(body))
    declared = _sub_plan_dependencies(body)
    inferred = {c: set(creators) for c, creators in evidence.items()}
    cycles = find_dependency_cycles(_dependency_graph(declared, inferred))
    component_of = {n: i for i, comp in enumerate(cycles) for n in comp}
    missing: dict[int, dict[int, list[str]]] = {}
    for consumer in sorted(evidence):
        for creator in sorted(evidence[consumer]):
            if creator in declared.get(consumer, set()):
                continue
            if consumer in component_of and component_of.get(creator) == component_of[consumer]:
                continue
            missing.setdefault(consumer, {})[creator] = evidence[consumer][creator]
    return missing


_SUB_PLAN_DEP_FORMAT_ID = "b1_milestone_subdesign.sub_plan_dep_format"
_DEP_CELL_TOKEN_ONLY_RE = re.compile(r"[\d#,\s\N{IDEOGRAPHIC COMMA}]+")


def _dep_cell_needs_format_fix(cell: str) -> bool:
    stripped = (cell or "").strip()
    if not stripped:
        return False
    if stripped == get_config().language.no_deps_word:
        return False
    if not _DEP_CELL_TOKEN_ONLY_RE.fullmatch(stripped):
        return False
    from issuesmith.contract import PLAN_DEP_REF_RE

    return any(not m.group(0).startswith("#") for m in PLAN_DEP_REF_RE.finditer(stripped))


def _check_sub_plan_dep_format(body: str) -> list[Violation]:
    """Depends-on cells with bare row numbers must use ``#N`` (#4929)."""
    cfg = get_config()
    milestone = get_section(body, cfg.sections["milestone"])
    if not milestone:
        return []
    plan_match = re.search(
        rf"###\s+{re.escape(cfg.sections['sub_plan'])}\s*\n(.*?)(?=^###|\Z)",
        milestone,
        re.MULTILINE | re.DOTALL,
    )
    if not plan_match:
        return []
    rows = parse_table_rows(plan_match.group(1))
    if len(rows) <= 1:
        return []
    dep_column = cfg.language.sub_plan_columns[4]
    dep_i = next((i for i, cell in enumerate(rows[0]) if dep_column in cell), None)
    if dep_i is None:
        return []
    violations: list[Violation] = []
    for row in rows[1:]:
        if len(row) <= dep_i or not row[0].strip().isdigit():
            continue
        cell = row[dep_i]
        if not _dep_cell_needs_format_fix(cell):
            continue
        row_num = int(row[0].strip())
        snippet = cell.strip()
        violations.append(
            Violation(
                rule_id=_SUB_PLAN_DEP_FORMAT_ID,
                severity="fail",
                message=(
                    f'sub plan row {row_num} depends-on cell "{snippet}" '
                    "must list rows as #N"
                ),
                location=f"## {cfg.sections['milestone']}",
                auto_fixable=True,
                fix_hint="",
            )
        )
    return violations


def apply_sub_plan_dep_format_fixes(body: str) -> tuple[str, list[str]]:
    """Normalize bare row numbers in depends-on cells to ``#N`` (#4929)."""
    cfg = get_config()
    milestone = re.search(
        rf"^##\s+{re.escape(cfg.sections['milestone'])}\s*\n", body, re.MULTILINE
    )
    if not milestone:
        return body, []
    plan = re.compile(
        rf"^###\s+{re.escape(cfg.sections['sub_plan'])}\s*\n(.*?)(?=^#{{1,3}}\s|\Z)",
        re.MULTILINE | re.DOTALL,
    ).search(body, milestone.end())
    if not plan:
        return body, []
    dep_column = cfg.language.sub_plan_columns[4]
    lines = plan.group(1).splitlines(keepends=True)
    dep_i: int | None = None
    num_i = 0
    changed = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|[\s:|-]+\|$", stripped):
            continue
        cells = parse_table_rows(stripped)[0]
        if dep_i is None:
            dep_i = next((i for i, cell in enumerate(cells) if dep_column in cell), None)
            if dep_i is None:
                return body, []
            continue
        if len(cells) <= dep_i or not cells[num_i].strip().isdigit():
            continue
        content = line.rstrip("\r\n")
        start, end = _table_cell_spans(content)[dep_i]
        old_cell = content[start:end]
        if not _dep_cell_needs_format_fix(old_cell):
            continue
        new_cell = ", ".join(f"#{n}" for n in plan_dep_refs(old_cell.strip()))
        lines[idx] = content[:start] + f" {new_cell} " + content[end:] + line[len(content) :]
        changed = True
    if not changed:
        return body, []
    new_body = body[: plan.start(1)] + "".join(lines) + body[plan.end(1) :]
    return new_body, [_SUB_PLAN_DEP_FORMAT_ID]


def _table_cell_spans(line: str) -> list[tuple[int, int]]:
    """``(start, end)`` of each cell of a ``| a | b |`` row (same split as parse_table_rows)."""
    bounds: list[int] = []
    in_tick = False
    for i, ch in enumerate(line):
        if ch == "`" and (in_tick or "`" in line[i + 1 :]):
            in_tick = not in_tick
        if ch == "|" and not in_tick:
            bounds.append(i)
    edges = [-1, *bounds, len(line)]
    spans = [(a + 1, b) for a, b in zip(edges, edges[1:])]
    if spans and not line[slice(*spans[0])].strip():
        spans = spans[1:]
    if spans and not line[slice(*spans[-1])].strip():
        spans = spans[:-1]
    return spans


def apply_inferred_dependency_fixes(body: str) -> tuple[str, list[str]]:
    """Add missing inferred dependencies to the sub plan; return ``(body, applied rule_ids)``.

    Only the depends-on cell of affected data rows changes; edges that would close a
    cycle are never written. Idempotent (#4825).
    """
    missing = _missing_dependencies(body)
    if not missing:
        return body, []
    cfg = get_config()
    milestone = re.search(
        rf"^##\s+{re.escape(cfg.sections['milestone'])}\s*\n", body, re.MULTILINE
    )
    if not milestone:
        return body, []
    plan = re.compile(
        rf"^###\s+{re.escape(cfg.sections['sub_plan'])}\s*\n(.*?)(?=^#{{1,3}}\s|\Z)",
        re.MULTILINE | re.DOTALL,
    ).search(body, milestone.end())
    if not plan:
        return body, []
    dep_column = cfg.language.sub_plan_columns[4]
    lines = plan.group(1).splitlines(keepends=True)
    dep_i: int | None = None
    num_i = 0
    changed = False
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|[\s:|-]+\|$", stripped):
            continue
        cells = parse_table_rows(stripped)[0]
        if dep_i is None:
            dep_i = next((i for i, cell in enumerate(cells) if dep_column in cell), None)
            if dep_i is None:
                return body, []
            continue
        if len(cells) <= dep_i or not cells[num_i].strip().isdigit():
            continue
        consumer = int(cells[num_i].strip())
        if consumer not in missing:
            continue
        content = line.rstrip("\r\n")
        spans = _table_cell_spans(content)
        if len(spans) <= dep_i:
            continue
        start, end = spans[dep_i]
        numbers = {int(n) for n in re.findall(r"\d+", content[start:end])}
        numbers |= set(missing[consumer])
        cell = ", ".join(f"#{n}" for n in sorted(numbers))
        lines[idx] = content[:start] + f" {cell} " + content[end:] + line[len(content):]
        changed = True
    if not changed:
        return body, []
    new_body = body[: plan.start(1)] + "".join(lines) + body[plan.end(1) :]
    return new_body, [_DEPENDENCY_MISSING_ID]


class B1MilestoneSubdesignRules:
    def check(self, body: str, labels: list[str]) -> list[Violation]:
        if "scope:milestone" not in labels:
            return []

        violations: list[Violation] = []
        violations.extend(self._check_sub_count(body))
        sub_blocks = extract_sub_blocks(body)
        for sub_num, block in sub_blocks:
            violations.extend(self._check_required_subsections(sub_num, block))
            violations.extend(self._check_table_schema(sub_num, block))
            violations.extend(self._check_repo_column(body, sub_num, block))
            violations.extend(self._check_change_paths_readable(body, sub_num, block))
            violations.extend(self._check_sub_ac(sub_num, block))
        violations.extend(self._check_file_union(body, sub_blocks))
        violations.extend(self._check_impact_scope(body, sub_blocks))
        violations.extend(self._check_deletion_reference_orphan(body, sub_blocks))
        violations.extend(_check_sub_plan_dep_format(body))
        dependencies = _sub_plan_dependencies(body)
        violations.extend(self._check_behavior_test_in_sibling(sub_blocks, dependencies))
        violations.extend(self._check_inferred_dependencies(body, sub_blocks))
        for sub_num, block in sub_blocks:
            violations.extend(self._check_sub_ac_contradiction(sub_num, block))
            violations.extend(
                self._check_sub_order_without_dependency(sub_num, block, dependencies)
            )
            violations.extend(self._check_placeholder_design(sub_num, block))
        return violations

    def _check_placeholder_design(self, sub_num: int, block: str) -> list[Violation]:
        """Detect scope_size promotion placeholders that still need real sub design (#4915)."""
        cfg = get_config()
        sections = cfg.sections
        changed = sections["changed_files"]
        ac = sections["acceptance_criteria"]
        skip = {changed, ac}
        reasons: list[str] = []
        for name in cfg.sub_design_subsections:
            if name in skip:
                continue
            if _subsection_body_starts_with_marker(_subsection_text(block, name)):
                reasons.append("scope/design policy marker")
                break
        if any(
            content.strip() == PLACEHOLDER_CHANGE_CONTENT
            for _, _, _, content in change_rows_with_content(block)
        ):
            reasons.append("change content")
        if _ac_items_are_template_only(_extract_ac_items(block)):
            reasons.append("template-only AC")
        if not reasons:
            return []
        fix_hint = (
            f"Replace the promoted placeholder in Sub {sub_num}: write real scope and"
            " design policy text instead of paragraphs starting with"
            f" `{PLACEHOLDER_DESIGN_MARKER}`; replace change-table cells that say"
            f" `{PLACEHOLDER_CHANGE_CONTENT}` with concrete change descriptions; add"
            " acceptance criteria beyond the three promotion templates (concern path"
            " list, required subsections, parent union). Derive scope, design policy,"
            " change content and AC from the parent ## Design section and the parent"
            f" change table. Do not change the `####` sub header, change-table paths,"
            " or YAML blocks."
        )
        return [
            Violation(
                rule_id=PLACEHOLDER_DESIGN_ID,
                severity="fail",
                message=(
                    f"Sub {sub_num}: promoted placeholder design ({', '.join(reasons)})"
                ),
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=fix_hint,
            )
        ]

    def _check_sub_count(self, body: str) -> list[Violation]:
        cfg = get_config()
        sections = cfg.sections
        sub_prefix = cfg.language.sub_header_prefix
        plan_count = _count_sub_plan_rows(body)
        if plan_count is None:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_plan_missing",
                severity="fail",
                message=(
                    f"no ### {sections['sub_plan']} table under "
                    f"## {sections['milestone']}"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]
        sub_headers = _SUB_HEADER_RE.findall(
            get_section(body, sections["design"]) or ""
        )
        header_count = len(sub_headers)
        if plan_count != header_count:
            return [Violation(
                rule_id="b1_milestone_subdesign.sub_count_mismatch",
                severity="fail",
                message=(
                    f"{sections['sub_plan']} table row count ({plan_count}) does not match"
                    f" the #### {sub_prefix}N header count ({header_count})"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=(
                    f"For every `{sections['sub_plan']}` row N, add a "
                    f"`#### {sub_prefix}N: <title>` block "
                    f"under `## {sections['design']}` with the required subsections "
                    + " / ".join(
                        f"**{name}**" for name in cfg.sub_design_subsections
                    )
                    + ". Do not use the spaced `Sub N` header form "
                    "(milestone_consistency.sub_header_english rejects them)."
                ),
            )]
        return []

    def _check_required_subsections(self, sub_num: int, block: str) -> list[Violation]:
        violations: list[Violation] = []
        for name in get_config().sub_design_subsections:
            if not re.search(rf"\*\*{re.escape(name)}\*\*", block):
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.subsection_missing",
                    severity="fail",
                    message=f"Sub {sub_num}: required subsection **{name}** is missing",
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations

    def _check_table_schema(self, sub_num: int, block: str) -> list[Violation]:
        changed = get_config().sections["changed_files"]
        table_match = re.search(
            rf"\*\*{re.escape(changed)}\*\*:?\s*\n(.*?)(?=\*\*|\Z)",
            block,
            re.DOTALL,
        )
        if not table_match:
            return []
        rows = parse_table_rows(table_match.group(1))
        if not rows:
            return [Violation(
                rule_id="b1_milestone_subdesign.table_schema",
                severity="fail",
                message=f"Sub {sub_num}: the {changed} table is empty",
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=None,
            )]
        header = rows[0]
        expected = get_config().language.change_table_columns
        if len(header) != 4:
            return [Violation(
                rule_id="b1_milestone_subdesign.table_schema",
                severity="fail",
                message=(
                    f"Sub {sub_num}: the {changed} table does not have the 4-column schema"
                    f" ({' / '.join(expected)}; got {len(header)} columns)"
                ),
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=None,
            )]
        for col, exp in zip(header, expected):
            if exp not in col:
                return [Violation(
                    rule_id="b1_milestone_subdesign.table_schema",
                    severity="fail",
                    message=(
                        f"Sub {sub_num}: invalid {changed} table column names"
                        f" (expected: {' / '.join(expected)})"
                    ),
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=None,
                )]
        return []

    def _check_repo_column(self, body: str, sub_num: int, block: str) -> list[Violation]:
        allowed = _allowed_repos(body)
        if not allowed:
            return []
        violations: list[Violation] = []
        for repo, path, _ in _extract_paths_from_change_table(block):
            if repo not in allowed:
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.repo_mismatch",
                    severity="fail",
                    message=(
                        f"Sub {sub_num}: repository column `{repo}` does not match"
                        f" the target_repo / host_allow_paths of any metadata block"
                        f" ({path})"
                    ),
                    location=_sub_location(sub_num),
                    auto_fixable=True,
                    fix_hint=(
                        "add a separate ```yaml metadata block for this repo at the top of the"
                        " body (keep the existing blocks unchanged):\n"
                        f"target_repo: {repo}\nbase_branch: main\nallow_paths:\n  - {path}"
                    ),
                ))
        return violations

    def _check_change_paths_readable(
        self, body: str, sub_num: int, block: str
    ) -> list[Violation]:
        """R3 parity for SUB1: the child allow_paths are derived from this table.

        SUB1 (issuesmith.milestone) calls change_paths_for_repo(block, row_repo)
        and fails the row when it returns []. This gate runs the same call at B1
        so an unreadable table is caught before any child Issue exists.
        """
        table_repos = sorted(
            {repo for repo, _, _ in extract_change_table_rows(block) if repo}
        )
        if table_repos:
            repos = table_repos
        else:
            allowed = sorted(_allowed_repos(body))
            repos = allowed[:1] or [""]
        violations: list[Violation] = []
        for repo in repos:
            if change_paths_for_repo(block, repo or None):
                continue
            cfg = get_config()
            changed = cfg.sections["changed_files"]
            repository_col, file_path_col = cfg.language.change_table_columns[:2]
            header = "| " + " | ".join(cfg.language.change_table_columns) + " |"
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.change_paths_unreadable",
                severity="fail",
                message=(
                    f"Sub {sub_num}: no file paths for `{repo or '(no repo)'}` can be"
                    f" extracted from the {changed} table (SUB1 builds the child"
                    " allow_paths with the same extraction, so no child can be created)"
                ),
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=(
                    f"put the 4-column table {header} right after **{changed}**:,"
                    f" with owner/repo in the {repository_col} column and a path containing"
                    f" / in the {file_path_col} column"
                ),
            ))
        return violations

    def _check_sub_ac(self, sub_num: int, block: str) -> list[Violation]:
        violations: list[Violation] = []
        ac = get_config().sections["acceptance_criteria"]
        items = _extract_ac_items(block)
        if len(items) < 3:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.ac_count",
                severity="fail",
                message=f"Sub {sub_num}: {ac} has {len(items)} item(s) (3 or more required)",
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=None,
            ))
        vague_words = get_config().language.vague_ac_words
        for item in items:
            for word in vague_words:
                if word in item:
                    violations.append(Violation(
                        rule_id="b1_milestone_subdesign.ac_vague_word",
                        severity="fail",
                        message=f"Sub {sub_num}: {ac} contains the vague word `{word}`",
                        location=_sub_location(sub_num),
                        auto_fixable=False,
                        fix_hint=None,
                    ))
                    break
        return violations

    def _check_file_union(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        sections = get_config().sections
        changed = sections["changed_files"]
        design = sections["design"]
        parent_paths = _extract_parent_change_paths(body)
        if not parent_paths:
            # Milestone parents may have no changed-files section; the blocks'
            # allow_paths are then the parent's path set (#4076).
            parent_paths = _allow_paths_union(body)
        sub_paths: list[str] = []
        for _, block in sub_blocks:
            for _, path, _ in _extract_paths_from_change_table(block):
                sub_paths.append(path)
        sub_set = set(sub_paths)
        violations: list[Violation] = []
        missing_in_subs = parent_paths - sub_set
        missing_in_parent = sub_set - parent_paths
        if missing_in_subs:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_missing_in_subs",
                severity="fail",
                message=(
                    f"paths in the parent {changed} but in no sub: "
                    + ", ".join(sorted(missing_in_subs))
                ),
                location=f"## {changed}",
                auto_fixable=False,
                fix_hint=None,
            ))
        if missing_in_parent:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_missing_in_parent",
                severity="fail",
                message=(
                    f"paths in a sub {changed} but not in the parent: "
                    + ", ".join(sorted(missing_in_parent))
                ),
                location=f"## {changed}",
                auto_fixable=False,
                fix_hint=None,
            ))
        duplicates = {p for p in sub_paths if sub_paths.count(p) > 1}
        if duplicates:
            violations.append(Violation(
                rule_id="b1_milestone_subdesign.file_union_duplicate",
                severity="fail",
                message=f"{changed} duplicated across subs: " + ", ".join(sorted(duplicates)),
                location=f"## {design}",
                auto_fixable=False,
                fix_hint=None,
            ))
        return violations

    def _check_impact_scope(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        sections = get_config().sections
        impact_name = sections["impact_survey"]
        changed = sections["changed_files"]
        impact = get_section(body, impact_name)
        if not impact:
            return []
        allowed_paths = _extract_parent_change_paths(body)
        for _, block in sub_blocks:
            for _, path, _ in _extract_paths_from_change_table(block):
                allowed_paths.add(path)
        refs = _extract_file_refs_from_text(impact)
        violations: list[Violation] = []
        for ref in refs:
            if ref not in allowed_paths:
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.impact_scope_pollution",
                    severity="fail",
                    message=(
                        f"{impact_name} file reference `{ref}` is not in"
                        f" the parent or sub {changed}"
                    ),
                    location=f"## {impact_name}",
                    auto_fixable=False,
                    fix_hint=None,
                ))
        return violations

    def _check_deletion_reference_orphan(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        """B1 twin of ``scope_coupling.deletion_reference_uncovered`` (#4518).

        A deleted file whose referrers no sub change table owns makes the deleting
        sub fail P1 with nobody allowed to fix the referrers.
        """
        from issuesmith.gate_rules import scope_coupling

        keywords = scope_coupling._delete_move_keywords()
        if not any(
            any(word in change_type.lower() for word in keywords)
            for _, block in sub_blocks
            for _, _, change_type in _extract_paths_from_change_table(block)
        ):
            return []
        try:
            metadata = parse_issue_metadata(body)
        except Exception:
            return []
        if scope_coupling.resolve_scope_root(metadata, get_config()) is None:
            return []
        refs = scope_coupling.deletion_references_for_body(body)
        design = get_config().sections["design"]
        return [
            Violation(
                rule_id="b1_milestone_subdesign.deletion_reference_orphan",
                severity="fail",
                message=(
                    f"files referencing deleted `{path}` are in no sub change table: "
                    + ", ".join(files)
                ),
                location=f"## {design}",
                auto_fixable=False,
                fix_hint=(
                    "add each referrer to some sub's change table, or stage the deletion"
                    " (keep an alias first, delete it in the last sub)"
                ),
            )
            for path, files in refs.items()
        ]

    def _check_behavior_test_in_sibling(
        self,
        sub_blocks: list[tuple[int, str]],
        dependencies: dict[int, set[int]],
    ) -> list[Violation]:
        """Reject an impl file whose mirror test sits in a sibling sub (#4518).

        A test sub that declares the impl sub in its depends-on cell is allowed (#4745).
        """
        owned = [
            (sub_num, repo, path, change_type, content)
            for sub_num, block in sub_blocks
            for repo, path, change_type, content in change_rows_with_content(block)
        ]
        tests = [
            (sub_num, repo, path)
            for sub_num, repo, path, _, _ in owned
            if path.startswith("tests/")
        ]
        violations: list[Violation] = []
        for sub_num, repo, path, change_type, content in owned:
            if path.startswith("tests/"):
                continue
            is_deletion = _is_deletion_row(change_type, content)
            for test_sub, test_repo, test_path in tests:
                if test_sub == sub_num or test_repo != repo:
                    continue
                if not _sibling_test_matches(path, test_path, deletion=is_deletion):
                    continue
                if not is_deletion and sub_num in dependencies.get(test_sub, set()):
                    continue
                deletion_suffix = " (deletion)" if is_deletion else ""
                if is_deletion:
                    fix_hint = (
                        "move the test into the same sub's change table as the impl,"
                        " or merge the two subs"
                    )
                else:
                    fix_hint = (
                        "move the test into the same sub's change table as the impl,"
                        f" merge the two subs, or make Sub {test_sub} depend on Sub {sub_num}"
                    )
                violations.append(Violation(
                    rule_id="b1_milestone_subdesign.behavior_test_in_sibling",
                    severity="fail",
                    message=(
                        f"Sub {sub_num}: `{path}` is changed here but its test"
                        f" `{test_path}` is in Sub {test_sub}{deletion_suffix}"
                    ),
                    location=_sub_location(sub_num),
                    auto_fixable=False,
                    fix_hint=fix_hint,
                ))
        return violations

    def _check_inferred_dependencies(
        self,
        body: str,
        sub_blocks: list[tuple[int, str]],
    ) -> list[Violation]:
        """Sibling new-file references must be declared; the dependency graph is acyclic (#4825)."""
        evidence = _infer_dependency_evidence(sub_blocks)
        declared = _sub_plan_dependencies(body)
        inferred = {c: set(creators) for c, creators in evidence.items()}
        graph = _dependency_graph(declared, inferred)
        plan = get_config().sections["sub_plan"]
        dep_column = get_config().language.sub_plan_columns[4]
        violations: list[Violation] = []
        for consumer, creators in sorted(_missing_dependencies(body).items()):
            for creator, files in sorted(creators.items()):
                violations.append(Violation(
                    rule_id=_DEPENDENCY_MISSING_ID,
                    severity="fail",
                    message=(
                        f"Sub {consumer} references "
                        + ", ".join(f"`{f}`" for f in files)
                        + f" created by Sub {creator}, but its {plan} {dep_column}"
                        f" cell does not list #{creator}"
                    ),
                    location=_sub_location(consumer),
                    auto_fixable=True,
                    fix_hint=(
                        f"add #{creator} to the {dep_column} cell of {plan} row {consumer}"
                    ),
                ))
        for component in find_dependency_cycles(graph):
            walk = " -> ".join(str(n) for n in _cycle_path(graph, component))
            violations.append(Violation(
                rule_id=_DEPENDENCY_CYCLE_ID,
                severity="fail",
                message=f"sub dependency cycle: {walk}",
                location=f"## {get_config().sections['milestone']}",
                auto_fixable=False,
                fix_hint=(
                    "merge the subs in the cycle into one sub, or put a compatible stub on"
                    " the creator side and stage them producer -> consumer"
                ),
            ))
        return violations

    def _check_sub_ac_contradiction(self, sub_num: int, block: str) -> list[Violation]:
        """Reject a sub that says it fails tests alone yet requires a full pass (#4518)."""
        ac = get_config().sections["acceptance_criteria"]
        design_text = re.split(rf"\*\*{re.escape(ac)}\*\*", block, maxsplit=1)[0]
        if not any(pattern.search(design_text) for pattern in _STANDALONE_FAIL_RES):
            return []
        full_suite = any(_FULL_SUITE_AC_RE.search(item) for item in _extract_ac_items(block))
        if not full_suite:
            return []
        return [Violation(
            rule_id="b1_milestone_subdesign.sub_ac_contradiction",
            severity="fail",
            message=(
                f"Sub {sub_num}: the design says this sub fails the tests on its own,"
                f" but its {ac} require the whole test suite to pass"
            ),
            location=_sub_location(sub_num),
            auto_fixable=False,
            fix_hint=(
                f"drop the whole-suite requirement from {ac}, or remove the"
                " 'another sub handles it' split (move the referrers into this sub,"
                " or stage the change)"
            ),
        )]

    def _check_sub_order_without_dependency(
        self, sub_num: int, block: str, dependencies: dict[int, set[int]]
    ) -> list[Violation]:
        """Reject a sub ordered after a sibling the depends-on column omits (#4818)."""
        if not dependencies:
            return []
        cfg = get_config()
        language = cfg.language
        ac = cfg.sections["acceptance_criteria"]
        design_text = re.split(rf"\*\*{re.escape(ac)}\*\*", block, maxsplit=1)[0]
        sub_ref_re = re.compile(re.escape(language.sub_header_prefix) + r"\s*(\d+)", re.IGNORECASE)
        order_res = [
            re.compile(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", re.IGNORECASE)
            for word in language.order_after_words
        ]
        declared = dependencies.get(sub_num, set())
        ordered: list[int] = []
        for line in design_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("|") or _SUB_HEADER_RE.match(stripped):
                continue
            if not any(pattern.search(line) for pattern in order_res):
                continue
            for match in sub_ref_re.finditer(line):
                other = int(match.group(1))
                if other != sub_num and other not in declared and other not in ordered:
                    ordered.append(other)
        return [
            Violation(
                rule_id="b1_milestone_subdesign.sub_order_without_dependency",
                severity="fail",
                message=(
                    f"Sub {sub_num}: the design orders this sub after Sub {other},"
                    f" but the sub-issue plan's depends-on column for row {sub_num}"
                    f" does not list #{other}"
                ),
                location=_sub_location(sub_num),
                auto_fixable=False,
                fix_hint=(
                    f"add #{other} to row {sub_num}'s depends-on column in the"
                    " sub-issue plan, or remove the ordering sentence"
                ),
            )
            for other in ordered
        ]


GATE_REGISTRY["b1_milestone_subdesign"] = B1MilestoneSubdesignRules
