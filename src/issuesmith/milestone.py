"""Milestone chain automation and SUB1 split-plan helpers (#4276)."""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ghdag.forge import ForgePort, get_forge

from issuesmith.body_editor import (
    count_heading,
    filter_section_by_paths,
    get_section,
    get_section_by_keyword,
    get_subsections,
)
from issuesmith.config import IssuesmithConfig, MilestoneChainConfig, StepConfig, get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.contract import (
    StepContext,
    StepResult,
    change_paths_for_repo,
    parse_table_rows,
    sub_block,
)
from issuesmith.dep_extractor import check_dependencies, extract_dependencies
from issuesmith.forge_api import api_request
from issuesmith.queue_store import QueueSnapshot, QueueStore
from issuesmith.queue_triage import (
    DONE_LABEL,
    READY_LABEL,
    RUNNING_LABEL,
    get_terminal_without_merge,
    label_names,
    parse_frontmatter_fields,
)

_MILESTONE_LABEL = "scope:milestone"


def _build_sub_labels() -> frozenset[str]:
    return frozenset(
        lab
        for lab in (
            READY_LABEL.get("sub"),
            RUNNING_LABEL.get("sub"),
            DONE_LABEL.get("sub"),
        )
        if lab
    )


def _build_milestone_idle_ok_labels() -> frozenset[str]:
    return frozenset(
        lab
        for lab in (
            DONE_LABEL.get("draft"),
            DONE_LABEL.get("sub"),
            RUNNING_LABEL.get("sub"),
        )
        if lab
    )


_SUB_LABELS: frozenset[str] = _build_sub_labels()
_MILESTONE_IDLE_OK_LABELS: frozenset[str] = _build_milestone_idle_ok_labels()
_CJK_PLACEHOLDER_RE = re.compile(
    r"(プレースホルダ|プレースホルダー|未記入|TBD|TODO|FIXME|XXX|ＸＸＸ|要記入|ここに)"
)
# V3: 説明文中の語は無視し、単独行・YAML・見出し直下セクションのみ検出する。
_STANDALONE_PLACEHOLDER_RE = re.compile(
    r"^(?:[-*]\s*)?(?:プレースホルダ|プレースホルダー|未記入|TBD|TODO|FIXME|XXX|ＸＸＸ|要記入|ここに)\s*$"
)
_YAML_FENCE_RE = re.compile(r"```ya?ml\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
_HEADING_SPLIT_RE = re.compile(r"(?m)^(#{1,6}\s+.+)$")
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")
_PLAN_REF_RE = re.compile(r"^\|\s*(\d+)\s*\|")
# 解決済み Issue 参照（3 桁以上）。plan ref（サブ N の連番）と区別する。
_RESOLVED_ISSUE_REF_RE = re.compile(r"#(\d{3,})\b")
_TERMINAL_NEGATIVE_OUTCOMES = frozenset({"rejected", "dequeued", "skipped"})


@dataclass
class ChildValidation:
    issue: int
    passed: bool
    failures: list[str]


@dataclass
class ValidateChildrenResult:
    passed: bool
    results: list[ChildValidation]


def milestone_last_issue_terminal_ok(labels: set[str]) -> bool:
    """Return True when an OPEN milestone parent should not trigger last_issue halt."""
    return _MILESTONE_LABEL in labels and bool(labels & _MILESTONE_IDLE_OK_LABELS)


def has_sub_labels(labels: set[str]) -> bool:
    return bool(labels & _SUB_LABELS)


def _issue_is_terminal(client: ForgePort, issue_number: int) -> bool:
    return _classify_child_terminal(client, issue_number) != "open"


def _classify_child_terminal(client: ForgePort, issue_number: int) -> str:
    """Classify a child Issue for C4: open | merged | closed_without_merge."""
    try:
        issue = client.issue_get(issue_number, fields=["state", "labels"])
    except Exception:
        return "open"
    labels = label_names(issue)
    state = str(issue.get("state", "")).upper()
    if state != "CLOSED":
        return "open"
    _merge_done = DONE_LABEL.get("merge", "")
    if _merge_done and _merge_done in labels:
        return "merged"
    if labels & get_terminal_without_merge():
        return "closed_without_merge"
    return "open"


def _has_cp1_intentional_hold(comments: list[dict[str, Any]]) -> bool:
    brushup_idx = -1
    for idx, comment in enumerate(comments):
        body = str(comment.get("body") or "")
        if "PIPELINE_STATUS: BRUSHUP_DONE" in body:
            brushup_idx = idx
    for comment in comments[brushup_idx + 1 :]:
        body = str(comment.get("body") or "")
        if "## CP1 " not in body and "CP1_STATUS:" not in body:
            continue
        if "INTENTIONAL_HOLD: true" in body:
            return True
    return False


# Canonical extractors live in issuesmith.contract (R1).
_parse_table_rows = parse_table_rows
_extract_change_paths = change_paths_for_repo


def _plan_section(body: str) -> str | None:
    plan = get_config().sections["sub_plan"]
    match = re.search(
        rf"^###\s+{re.escape(plan)}\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def plan_section(body: str) -> str | None:
    """Return the configured split-plan subsection body, or None."""
    return _plan_section(body)


def normalize_plan_title(title: str) -> str:
    """Normalize plan/child titles for V1 matching (shared with normalize_sub_head style).

    Strips backticks, collapses full-width/half-width spaces, and trims edges.
    """
    text = str(title).replace("`", "")
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _expected_child_target_repo(
    parent_body: str, child: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Resolve V1 expected target_repo from the parent split plan, else parent YAML.

    Returns ``(expected_repo, error)``. When the plan has title+repo columns but no
    normalized title match, ``error`` is set and callers must not fall back.
    """
    parent_repo = _target_repo_from_body(parent_body)
    section = _plan_section(parent_body)
    if section is None:
        return parent_repo, None
    rows = _parse_table_rows(section)
    if len(rows) <= 1:
        return parent_repo, None
    header = rows[0]
    try:
        title_idx = next(i for i, cell in enumerate(header) if "タイトル" in cell)
    except StopIteration:
        return parent_repo, None
    try:
        repo_idx = next(i for i, cell in enumerate(header) if "対象リポジトリ" in cell)
    except StopIteration:
        return parent_repo, None

    child_title = str(child.get("title") or "")
    child_norm = normalize_plan_title(child_title)
    for row in rows[1:]:
        if len(row) <= title_idx:
            continue
        if normalize_plan_title(row[title_idx]) != child_norm:
            continue
        if len(row) <= repo_idx:
            return parent_repo, None
        value = row[repo_idx].strip().strip("`")
        return (value or parent_repo), None
    return None, f"V1 plan row not found for title {child_title!r}"


def _allow_paths_from_body(body: str) -> list[str]:
    data = parse_frontmatter_fields(body)
    allow = data.get("allow_paths")
    if isinstance(allow, list):
        return [str(item) for item in allow if isinstance(item, str)]
    return []


def _target_repo_from_body(body: str) -> str | None:
    data = parse_frontmatter_fields(body)
    repo = data.get("target_repo")
    return str(repo).strip() if isinstance(repo, str) and repo.strip() else None


def _milestone_number(issue: dict[str, Any]) -> int | None:
    milestone = issue.get("milestone")
    if isinstance(milestone, dict):
        number = milestone.get("number")
        if isinstance(number, int):
            return number
    return None


def link_sub_issue(client: ForgePort, parent_number: int, child_number: int) -> bool:
    """Link ``child_number`` as a GitHub sub-issue of ``parent_number``.

    Resolves the child's Issue ``id`` (not ``number``) via ``issue_get``, then
    calls ``client.add_sub_issue``. Returns True on success (including 422
    duplicate treated as idempotent). Never raises: API failures are logged to
    stderr and return False so the legacy milestone path can still proceed.
    """
    try:
        child = client.issue_get(child_number, fields=["id", "number"])
        child_id = child.get("id")
        if not isinstance(child_id, int):
            print(
                f"warning: link_sub_issue #{parent_number}<-#{child_number}: "
                f"child has no integer id ({child_id!r})",
                file=sys.stderr,
            )
            return False
        add = getattr(client, "add_sub_issue", None)
        if add is None:
            print(
                f"warning: link_sub_issue #{parent_number}<-#{child_number}: "
                "client has no add_sub_issue",
                file=sys.stderr,
            )
            return False
        add(parent_number, child_id)
        return True
    except Exception as exc:
        # Defense in depth: ghdag add_sub_issue already treats 422 as success,
        # but swallow raised 422 the same way for alternate clients / older builds.
        if getattr(exc, "status_code", None) == 422:
            return True
        print(
            f"warning: link_sub_issue #{parent_number}<-#{child_number} failed: {exc}",
            file=sys.stderr,
        )
        return False


def ensure_sub1_binding(client: ForgePort, parent_number: int, child_number: int) -> bool:
    """SUB1 gate: continue when milestone is set and/or sub-issue link succeeds.

    Attempts ``link_sub_issue``. Returns True when the parent has a milestone
    object and/or the link succeeded. When both are unavailable (the #3059
    failure mode with a failed link), posts an error comment and returns False
    so SUB1 can stop. Wiring into the sub-dispatch template is done in a later sub-issue.
    """
    linked = link_sub_issue(client, parent_number, child_number)
    try:
        parent = client.issue_get(parent_number, fields=["milestone", "number"])
    except Exception:
        parent = {}
    if _milestone_number(parent) is not None:
        return True
    if linked:
        return True
    _ensure_parent_comment(
        client,
        parent_number,
        "## SUB1 エラー: milestone 未設定かつサブイシューリンク失敗\n\n"
        f"親 Issue に milestone が設定されておらず、子 #{child_number} の"
        "サブイシューリンクにも失敗しました。",
        "<!-- issuesmith:sub1:no-milestone-no-sub-link -->",
    )
    return False


def _paths_covered(allow_paths: list[str], paths: list[str]) -> list[str]:
    missing: list[str] = []
    for path in paths:
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allow_paths):
            missing.append(path)
    return missing


_CJK_PATH_CHAR_RE = re.compile(r"[　-鿿＀-￯]")
_PATH_EXT_RE = re.compile(r"\.[A-Za-z0-9]+$")


def is_cjk_placeholder_path(path: str) -> bool:
    """True when ``path`` looks like a CJK placeholder (no slash, no extension)."""
    return bool(_CJK_PATH_CHAR_RE.search(path)) and "/" not in path and not _PATH_EXT_RE.search(path)


def check_v1_target_repo(
    child_repo: str | None,
    expected_repo: str | None,
    supported: frozenset[str] | set[str],
) -> list[str]:
    """V1: child target_repo matches expected and both are in supported_repos."""
    failures: list[str] = []
    if expected_repo and child_repo != expected_repo:
        failures.append(
            f"V1 target_repo mismatch: expected {expected_repo!r}, got {child_repo!r}"
        )
    if child_repo and child_repo not in supported:
        failures.append(
            f"V1 target_repo unsupported: {child_repo!r} not in supported_repos"
        )
    if expected_repo and expected_repo not in supported:
        failures.append(
            f"V1 expected target_repo unsupported: {expected_repo!r} not in supported_repos"
        )
    return failures


def check_v2_allow_paths(allow_paths: list[str], paths: list[str]) -> list[str]:
    """V2: every change-table path must be covered by allow_paths (fnmatch)."""
    missing = _paths_covered(allow_paths, paths)
    if missing:
        return [f"V2 allow_paths missing: {', '.join(missing)}"]
    return []


def _is_placeholder_only_section(text: str) -> bool:
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("<!--")
    ]
    if not lines:
        return False
    return all(
        _STANDALONE_PLACEHOLDER_RE.match(ln) or bool(_CJK_PLACEHOLDER_RE.fullmatch(ln))
        for ln in lines
    )


def _body_has_restricted_placeholder(body: str) -> bool:
    """True when placeholder tokens appear in YAML / standalone lines / heading sections."""
    for match in _YAML_FENCE_RE.finditer(body):
        if _CJK_PLACEHOLDER_RE.search(match.group(1)):
            return True
    for line in body.splitlines():
        if _STANDALONE_PLACEHOLDER_RE.match(line.strip()):
            return True
    parts = _HEADING_SPLIT_RE.split(body)
    # parts: [preamble, heading, content, heading, content, ...]
    idx = 1
    while idx < len(parts):
        content = parts[idx + 1] if idx + 1 < len(parts) else ""
        if _is_placeholder_only_section(content):
            return True
        idx += 2
    return False


def check_v3_cjk_placeholders(
    *,
    body: str | None = None,
    allow_paths: list[str] | None = None,
) -> list[str]:
    """V3: reject unfilled placeholder tokens (not prose mentions) and CJK allow_paths."""
    failures: list[str] = []
    if body is not None and _body_has_restricted_placeholder(body):
        failures.append("V3 CJK placeholder detected")
    if allow_paths:
        for path in allow_paths:
            if is_cjk_placeholder_path(path):
                failures.append(f"V3: CJK プレースホルダー ({path})")
    return failures


def _dependency_refs_unresolved(body: str) -> list[str]:
    deps_heading = get_config().sections["dependencies"]
    section_match = re.search(
        rf"^##\s+{re.escape(deps_heading)}\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return []
    section = section_match.group(1)
    failures: list[str] = []
    for line in section.splitlines():
        if not _TABLE_ROW_RE.match(line.strip()):
            continue
        if _TABLE_SEPARATOR_RE.match(line.strip()):
            continue
        if _RESOLVED_ISSUE_REF_RE.search(line):
            # 先頭セルが連番（`| # | 依存先 | 状態 |` 形式の行番号）でも、同じ行に
            # 解決済みの `#NNNN` があれば依存は解決している。2026-09-10、SUB1 が
            # 生成した #3000 の `| 1 | #2999 (...) | OPEN |` を plan ref #1 と誤判定し
            # milestone chain が validation failed で停止した。
            continue
        for match in _PLAN_REF_RE.finditer(line):
            failures.append(f"unresolved plan ref #{match.group(1)} in dependency table")
    return failures


def validate_children(
    parent: dict[str, Any],
    children: list[dict[str, Any]],
    *,
    client: ForgePort,
) -> ValidateChildrenResult:
    parent_body = str(parent.get("body") or "")
    parent_milestone = _milestone_number(parent)
    supported = get_config().supported_repos
    results: list[ChildValidation] = []

    for child in children:
        child_num = child.get("number")
        if not isinstance(child_num, int):
            continue
        body = str(child.get("body") or "")
        labels = label_names(child)
        failures: list[str] = []

        child_repo = _target_repo_from_body(body)
        expected_repo, plan_err = _expected_child_target_repo(parent_body, child)
        if plan_err:
            failures.append(plan_err)
        else:
            failures.extend(check_v1_target_repo(child_repo, expected_repo, supported))

        allow_paths = _allow_paths_from_body(body)
        child_paths = _extract_change_paths(body, repo=child_repo)
        failures.extend(check_v2_allow_paths(allow_paths, child_paths))
        failures.extend(check_v3_cjk_placeholders(body=body))

        unresolved = _dependency_refs_unresolved(body)
        if unresolved:
            failures.extend(unresolved)
        deps = extract_dependencies(body)
        for dep in deps:
            try:
                client.issue_get(dep, fields=["number"])
            except Exception:
                failures.append(f"V4 dependency #{dep} not found")

        _draft_done = DONE_LABEL.get("draft", "")
        if _draft_done and _draft_done not in labels:
            failures.append(f"V5 missing {_draft_done}")
        child_milestone = _milestone_number(child)
        if parent_milestone is not None and child_milestone != parent_milestone:
            failures.append(
                f"V5 milestone mismatch: child={child_milestone} parent={parent_milestone}"
            )

        results.append(
            ChildValidation(issue=child_num, passed=not failures, failures=failures)
        )

    passed = bool(results) and all(item.passed for item in results)
    return ValidateChildrenResult(passed=passed, results=results)


def _list_milestone_children(client: ForgePort, milestone_number: int) -> list[dict[str, Any]]:
    try:
        raw = api_request(
            client,
            f"issues?state=all&milestone={milestone_number}&per_page=100",
            paginate=True,
        )
    except Exception:
        try:
            raw = api_request(client, f"issues?state=all&milestone={milestone_number}&per_page=100")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    children: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("pull_request") is not None:
            continue
        children.append(item)
    return children


def _list_chain_children(
    client: ForgePort,
    parent_number: int,
    parent: dict[str, Any],
) -> list[dict[str, Any]]:
    """List children via ``list_sub_issues``, with milestone API fallback.

    Primary: ``client.list_sub_issues(parent_number)``.
    Fallback: when that returns empty and the parent has a milestone number,
    use ``_list_milestone_children`` (legacy chains without sub-issue links).
    """
    children: list[dict[str, Any]] = []
    list_fn = getattr(client, "list_sub_issues", None)
    if callable(list_fn):
        try:
            raw = list_fn(parent_number)
        except Exception:
            raw = []
        if isinstance(raw, list):
            children = [item for item in raw if isinstance(item, dict)]

    if not children:
        milestone_number = _milestone_number(parent)
        if milestone_number is not None:
            children = _list_milestone_children(client, milestone_number)

    return [child for child in children if child.get("number") != parent_number]


def _has_request(
    store: QueueStore | None, snap: QueueSnapshot, issue: int, phase: str
) -> bool:
    """True when an active or successfully-completed request exists for issue+phase.

    Completed requests with outcome in ``rejected`` / ``dequeued`` / ``skipped``
    do not count as already enqueued (chain may re-enqueue after deps resolve).
    """
    _ = store
    positive_completed = {
        rid
        for rid in snap.completed_request_ids
        if (snap.request_meta.get(rid) or {}).get("outcome") not in _TERMINAL_NEGATIVE_OUTCOMES
    }
    seen: set[str] = set(snap.active_order) | positive_completed
    for rid in seen:
        req = snap.requests.get(rid)
        if req is not None and req.issue == issue and req.phase == phase:
            return True
    return False


def _enqueue_chain(
    store: QueueStore,
    *,
    issue: int,
    phase: str,
    priority: str,
) -> None:
    store.enqueue(
        issue=issue,
        phase=phase,
        source="milestone-chain",
        actor_kind="automation",
        priority=priority,
        requested_by=["milestone-chain"],
        requested_at=datetime.now(timezone.utc).isoformat(),
    )


def _list_scope_milestone_parents(client: ForgePort) -> list[dict[str, Any]]:
    """Open issues labeled ``scope:milestone`` (candidate chain parents)."""
    try:
        raw = api_request(
            client,
            "issues?state=open&labels=scope:milestone&per_page=100",
            paginate=True,
        )
    except Exception:
        try:
            raw = api_request(client, "issues?state=open&labels=scope:milestone&per_page=100")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if item.get("pull_request") is not None:
            continue
        out.append(item)
    return out


def _candidate_parents(
    store: QueueStore,
    snap: QueueSnapshot,
    client: ForgePort,
) -> set[int]:
    candidates: set[int] = set()
    for key, entry in snap.milestone_chains.items():
        if isinstance(entry, dict) and entry.get("closed_parent"):
            continue
        try:
            candidates.add(int(key))
        except ValueError:
            continue
    for entry in snap.in_flight:
        issue = entry.get("issue")
        if isinstance(issue, int):
            candidates.add(issue)
    for item in _list_scope_milestone_parents(client):
        number = item.get("number")
        if isinstance(number, int):
            candidates.add(number)
    return candidates


def _list_open_milestones(client: ForgePort) -> list[dict[str, Any]]:
    """Deprecated: prefer ``_list_scope_milestone_parents``.

    Kept for fallback / external callers. ``advance_milestone_chains`` does not
    call this; it uses ``_list_scope_milestone_parents`` directly.
    """
    return _list_scope_milestone_parents(client)


def _halt_chain(store: QueueStore, parent: int, reason: str) -> None:
    store.update_milestone_chain(
        parent,
        {
            "stage": "halted",
            "halted_reason": reason,
        },
    )


def _ensure_parent_comment(client: ForgePort, parent: int, body: str, marker: str) -> None:
    try:
        comments = client.get_issue_comments(parent)
    except Exception:
        comments = []
    if not isinstance(comments, list):
        comments = []
    for comment in comments:
        if marker in str(comment.get("body") or ""):
            return
    client.issue_comment(parent, f"{body}\n\n{marker}")


def advance_milestone_chains(
    store: QueueStore,
    client: ForgePort,
    config: IssuesmithConfig | MilestoneChainConfig | None = None,
) -> None:
    if isinstance(config, MilestoneChainConfig):
        chain_cfg = config
    else:
        cfg = config or get_config()
        chain_cfg = cfg.milestone_chain
    if not chain_cfg.enabled:
        return

    pruned = set(store.prune_milestone_chains())
    snap = store.snapshot()
    # C0 (design-phase in_flight release) moved to queue.dispatch_one generic path (#2980).
    parents = _candidate_parents(store, snap, client)
    skipped_closed = 0

    for parent_num in sorted(parents):
        chain = store.get_milestone_chain(parent_num)
        if chain.get("stage") == "halted":
            continue
        if parent_num in pruned or chain.get("closed_parent"):
            skipped_closed += 1
            continue
        try:
            parent = client.issue_get(
                parent_num,
                fields=["state", "labels", "body", "milestone", "number", "title"],
            )
        except Exception:
            continue
        parent.setdefault("number", parent_num)
        labels = label_names(parent)
        if _MILESTONE_LABEL not in labels:
            continue

        # C1: design-phase done + CP1 intentional hold → enqueue sub.
        if (
            DONE_LABEL["draft"] in labels
            and not has_sub_labels(labels)
            and not _has_request(store, snap, parent_num, "sub")
        ):
            try:
                comments = client.get_issue_comments(parent_num)
            except Exception:
                comments = []
            if isinstance(comments, list) and _has_cp1_intentional_hold(comments):
                _enqueue_chain(
                    store,
                    issue=parent_num,
                    phase="sub",
                    priority=chain_cfg.child_priority,
                )
                store.update_milestone_chain(parent_num, {"stage": "sub_enqueued"})
                snap = store.snapshot()

        labels = label_names(parent)
        if DONE_LABEL["sub"] not in labels:
            continue

        snap = store.snapshot()
        chain = store.get_milestone_chain(parent_num)
        if chain.get("stage") == "halted":
            continue

        children = _list_chain_children(client, parent_num, parent)

        # C2: validate children and enqueue develop.
        if chain.get("stage") != "children_validated":
            if not children:
                _ensure_parent_comment(
                    client,
                    parent_num,
                    "子 Issue が未生成です。SUB1 の実行結果を確認してください。",
                    "<!-- issuesmith:milestone-chain:no-children -->",
                )
                _halt_chain(store, parent_num, "no children")
                continue

            validation = validate_children(parent, children, client=client)
            if not validation.passed:
                lines = ["子 Issue の検証に失敗しました。"]
                for item in validation.results:
                    if not item.passed:
                        lines.append(f"- #{item.issue}: {'; '.join(item.failures)}")
                _ensure_parent_comment(
                    client,
                    parent_num,
                    "\n".join(lines),
                    "<!-- issuesmith:milestone-chain:validation-failed -->",
                )
                _halt_chain(store, parent_num, "validation failed")
                continue

            store.update_milestone_chain(parent_num, {"stage": "children_validated"})
            if not chain_cfg.auto_develop:
                continue

            snap = store.snapshot()
            sorted_children = sorted(
                children,
                key=lambda item: int(item.get("number") or 0),
            )
            for child in sorted_children:
                child_num = child.get("number")
                if not isinstance(child_num, int):
                    continue
                if _has_request(store, snap, child_num, "develop"):
                    continue
                _enqueue_chain(
                    store,
                    issue=child_num,
                    phase="develop",
                    priority=chain_cfg.child_priority,
                )
            snap = store.snapshot()

        # C4: all children terminal → auto-close parent (or notify / halt).
        if not children:
            continue
        statuses: list[tuple[int, str]] = []
        for child in children:
            child_num = child.get("number")
            if not isinstance(child_num, int):
                continue
            statuses.append((child_num, _classify_child_terminal(client, child_num)))
        if not statuses or any(status == "open" for _, status in statuses):
            continue

        chain = store.get_milestone_chain(parent_num)
        without_merge = [num for num, status in statuses if status == "closed_without_merge"]
        if without_merge:
            first = without_merge[0]
            _md_label = DONE_LABEL.get("merge", "")
            _end_lbl = _md_label.split(":")[-1] if _md_label else "closed"
            _ensure_parent_comment(
                client,
                parent_num,
                f"確認待ち: #{first} が {_end_lbl} 以外で終了",
                "<!-- issuesmith:milestone-chain:closed-without-merge -->",
            )
            _halt_chain(store, parent_num, f"child closed without {_end_lbl}: #{first}")
            continue

        # All children completed the merge phase.
        if chain.get("closed_parent"):
            continue
        child_refs = ", ".join(f"#{num}" for num, _ in statuses)
        if chain_cfg.auto_close_parent:
            _ensure_parent_comment(
                client,
                parent_num,
                f"全サブイシュー完了 ({child_refs})",
                "<!-- issuesmith:milestone-chain:all-done -->",
            )
            client.issue_close(parent_num)
            store.update_milestone_chain(
                parent_num,
                {"notified_all_done": True, "closed_parent": True},
            )
        elif not chain.get("notified_all_done"):
            _ensure_parent_comment(
                client,
                parent_num,
                "全サブイシュー完了。親の close は人間が行う",
                "<!-- issuesmith:milestone-chain:all-done -->",
            )
            store.update_milestone_chain(parent_num, {"notified_all_done": True})

    print(
        f"[tick] milestone chains candidates={len(parents)} "
        f"skipped_closed={skipped_closed} pruned={len(pruned)}",
        file=sys.stderr,
    )


def _child_phase_label(labels: set[str]) -> str:
    for phase in (p.name for p in reversed(get_config().phases)):
        for kind, mapping in (
            ("done", DONE_LABEL),
            ("running", RUNNING_LABEL),
            ("ready", READY_LABEL),
        ):
            label = mapping.get(phase)
            if label and label in labels:
                return f"{phase}-{kind}"
    return "-"


def _sub_issues_summary(client: ForgePort, parent: int, parent_issue: dict[str, Any]) -> dict[str, int]:
    """Resolve sub_issues_summary from client helper or issue payload."""
    summary_fn = getattr(client, "sub_issues_summary", None)
    if callable(summary_fn):
        try:
            raw = summary_fn(parent)
        except Exception:
            raw = None
        if isinstance(raw, dict):
            return {
                "total": int(raw.get("total", 0) or 0),
                "completed": int(raw.get("completed", 0) or 0),
                "percent_completed": int(raw.get("percent_completed", 0) or 0),
            }
    embedded = parent_issue.get("sub_issues_summary")
    if isinstance(embedded, dict):
        return {
            "total": int(embedded.get("total", 0) or 0),
            "completed": int(embedded.get("completed", 0) or 0),
            "percent_completed": int(embedded.get("percent_completed", 0) or 0),
        }
    return {"total": 0, "completed": 0, "percent_completed": 0}


def _format_chain_waiting_line(
    *,
    parent: int,
    parent_issue: dict[str, Any],
    chain: dict[str, Any],
    store: QueueStore,
    snap: QueueSnapshot,
    client: ForgePort,
) -> str:
    """One-line summary of what the milestone chain is waiting for next."""
    stage = str(chain.get("stage") or "-")
    if chain.get("halted_reason"):
        return f"chain: halted ({chain['halted_reason']})"

    labels = label_names(parent_issue)

    def _active_req(issue: int, phase: str) -> str | None:
        for rid in snap.active_order:
            req = snap.requests.get(rid)
            if req is not None and req.issue == issue and req.phase == phase:
                return rid[:8]
        return None

    if DONE_LABEL["sub"] not in labels:
        rid = _active_req(parent, "sub")
        if rid:
            return f"chain: {stage} (sub request {rid} active, waiting for sub-done)"
        try:
            comments = client.get_issue_comments(parent)
        except Exception:
            comments = []
        if not isinstance(comments, list) or not _has_cp1_intentional_hold(comments):
            return f"chain: {stage} (hold comment missing, waiting for CP1 intentional hold)"
        return f"chain: {stage} (no sub request yet, waiting to enqueue sub)"

    children = _list_chain_children(client, parent, parent_issue)
    waiting_deps: list[int] = []
    pending_develop: list[int] = []
    for child in children:
        child_num = child.get("number")
        if not isinstance(child_num, int):
            continue
        body = str(child.get("body") or "")
        deps = extract_dependencies(body)
        if deps:
            result = check_dependencies(deps, client=client)
            if result.decision != "PASS":
                waiting_deps.append(child_num)
                continue
        if not _has_request(store, snap, child_num, "develop"):
            pending_develop.append(child_num)

    if waiting_deps:
        deps_txt = ", ".join(f"#{n}" for n in waiting_deps)
        rid = _active_req(parent, "sub")
        if rid:
            return f"chain: {stage} (sub request {rid} active, waiting for deps {deps_txt})"
        return f"chain: {stage} (waiting for deps {deps_txt})"
    if pending_develop:
        kids = ", ".join(f"#{n}" for n in pending_develop)
        return f"chain: {stage} (ready to enqueue develop for {kids})"
    return f"chain: {stage}"


def milestone_status(parent: int, *, client: ForgePort | None = None, store: QueueStore | None = None) -> int:
    client = client or get_forge()
    store = store or QueueStore()
    try:
        parent_issue = client.issue_get(
            parent,
            fields=["state", "labels", "body", "milestone", "number", "title", "sub_issues_summary"],
        )
    except Exception as exc:
        print(f"error: failed to fetch parent #{parent}: {exc}", file=sys.stderr)
        return 1

    snap = store.snapshot()
    chain = store.get_milestone_chain(parent)
    parent_state = str(parent_issue.get("state") or "-")
    print(f"milestone chain #{parent}: stage={chain.get('stage', '-')}")
    print(
        "  "
        + _format_chain_waiting_line(
            parent=parent,
            parent_issue=parent_issue,
            chain=chain,
            store=store,
            snap=snap,
            client=client,
        )
    )
    print(f"  state: {parent_state}")
    if chain.get("halted_reason"):
        print(f"  halted_reason: {chain['halted_reason']}")
    if chain.get("notified_all_done"):
        print("  notified_all_done: true")
    if chain.get("closed_parent"):
        print("  closed_parent: true")

    summary = _sub_issues_summary(client, parent, parent_issue)
    print(
        "  sub_issues_summary: "
        f"total={summary['total']} "
        f"completed={summary['completed']} "
        f"percent_completed={summary['percent_completed']}"
    )

    children = _list_chain_children(client, parent, parent_issue)
    if not children and _milestone_number(parent_issue) is None:
        print("  children: (none; no sub-issues and no milestone object on parent)")
        return 0

    print(f"{'#':>6}  {'title':<40}  {'target_repo':<28}  {'phase':<12}  {'deps':<8}  pr")
    print("-" * 100)
    for child in sorted(children, key=lambda item: int(item.get("number") or 0)):
        child_num = int(child.get("number") or 0)
        title = str(child.get("title") or "")[:40]
        labels = label_names(child)
        phase = _child_phase_label(labels)
        body = str(child.get("body") or "")
        target_repo = _target_repo_from_body(body) or ""
        deps = extract_dependencies(body)
        if deps:
            result = check_dependencies(deps, client=client)
            dep_state = "ok" if result.decision == "PASS" else "block"
        else:
            dep_state = "-"
        pr = "-"
        print(
            f"{child_num:>6}  {title:<40}  {target_repo:<28}  {phase:<12}  {dep_state:<8}  {pr}"
        )
    return 0


def milestone_resume(parent: int, *, store: QueueStore | None = None) -> int:
    store = store or QueueStore()
    if store.resume_milestone_chain(parent):
        print(f"milestone chain #{parent}: resumed")
        return 0
    print(f"error: chain for #{parent} is not halted", file=sys.stderr)
    return 1


def milestone_prune(*, dry_run: bool = False, store: QueueStore | None = None) -> int:
    store = store or QueueStore()
    pruned = store.prune_milestone_chains(dry_run=dry_run)
    label = "would prune" if dry_run else "pruned"
    if pruned:
        nums = ", ".join(f"#{n}" for n in pruned)
        print(f"milestone prune: {label} {nums} ({len(pruned)})")
    else:
        print(f"milestone prune: {label} 0")
    return 0


# ---------------------------------------------------------------------------
# SUB1 split-plan parser, dependency resolution, child body, preflight (#4276)
# ---------------------------------------------------------------------------

_DEFAULT_SUB1_TEMPLATE = "_sub1-body-order.md"
_SUB1_DEP_REF_RE = re.compile(r"#(\d+)")
_SUB1_NIKKI_PREFIX = "${NIKKI_ROOT}"

def _lt(*codepoints: int) -> str:
    return "".join(chr(codepoint) for codepoint in codepoints)

_LT_1 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x0043,
    0x0050, 0x0031, 0x0020, 0x672A, 0x901A, 0x904E, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_10 = _lt(
    0x30B5, 0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x30EA, 0x30F3, 0x30AF, 0x3067, 0x7D9A, 0x884C, 0x3057, 0x307E,
    0x3059, 0x3002, 0x005C, 0x006E
)
_LT_11 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x30B5,
    0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x5206, 0x5272, 0x8A08, 0x753B, 0x304C, 0x898B, 0x3064, 0x304B, 0x308A,
    0x307E, 0x305B, 0x3093
)
_LT_12 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x89AA,
    0x0020, 0x0059, 0x0041, 0x004D, 0x004C, 0x0020, 0x306B, 0x0020, 0x0074, 0x0061, 0x0072, 0x0067, 0x0065, 0x0074,
    0x005F, 0x0072, 0x0065, 0x0070, 0x006F, 0x0020, 0x304C, 0x3042, 0x308A, 0x307E, 0x305B, 0x3093, 0x005C, 0x006E,
    0x005C, 0x006E
)
_LT_13 = _lt(0x89AA, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x0023)
_LT_14 = _lt(
    0x0020, 0x306E, 0x0020, 0x0059, 0x0041, 0x004D, 0x004C, 0x0020, 0x30D6, 0x30ED, 0x30C3, 0x30AF, 0x306B, 0x0020,
    0x0060, 0x0074, 0x0061, 0x0072, 0x0067, 0x0065, 0x0074, 0x005F, 0x0072, 0x0065, 0x0070, 0x006F, 0x0060, 0x0020,
    0x30D5, 0x30A3, 0x30FC, 0x30EB, 0x30C9, 0x304C, 0x5FC5, 0x8981, 0x3067, 0x3059, 0x3002
)
_LT_15 = _lt(0x30BF, 0x30A4, 0x30C8, 0x30EB)
_LT_16 = _lt(0x5BFE, 0x8C61, 0x30EA, 0x30DD, 0x30B8, 0x30C8, 0x30EA)
_LT_17 = _lt(0x5185, 0x5BB9)
_LT_18 = _lt(0x4F9D, 0x5B58)
_LT_19 = _lt(0x30B5, 0x30D6)
_LT_2 = _lt(
    0x89AA, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x306E, 0x0020, 0x0043, 0x0050, 0x0031, 0x0020,
    0x30B2, 0x30FC, 0x30C8, 0x304C, 0x0020, 0x0046, 0x0041, 0x0049, 0x004C, 0xFF08, 0x0069, 0x006E, 0x0074, 0x0065,
    0x006E, 0x0074, 0x0069, 0x006F, 0x006E, 0x0061, 0x006C, 0x005F, 0x0068, 0x006F, 0x006C, 0x0064, 0x0020, 0x4EE5,
    0x5916, 0xFF09, 0x306E, 0x72B6, 0x614B, 0x3067, 0x3059, 0x3002
)
_LT_20 = _lt(
    0x003A, 0x0020, 0x0035, 0x0020, 0x5217, 0x8868, 0x3067, 0x5BFE, 0x8C61, 0x30EA, 0x30DD, 0x30B8, 0x30C8, 0x30EA,
    0x304C, 0x7A7A
)
_LT_21 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x65E2,
    0x5B58, 0x5B50, 0x0020, 0x0023
)
_LT_22 = _lt(
    0x0020, 0x306E, 0x30B5, 0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x30EA, 0x30F3, 0x30AF, 0x306B, 0x5931, 0x6557
)
_LT_23 = _lt(0x65E2, 0x5B58, 0x003A, 0x0020, 0x0023)
_LT_24 = _lt(0xFF08, 0x30B9, 0x30AD, 0x30C3, 0x30D7, 0xFF09)
_LT_25 = _lt(
    0x003A, 0x0020, 0x5909, 0x66F4, 0x5BFE, 0x8C61, 0x30D5, 0x30A1, 0x30A4, 0x30EB, 0x8868, 0x304B, 0x3089, 0x0020
)
_LT_26 = _lt(0x0020, 0x306E, 0x30D1, 0x30B9, 0x3092)
_LT_27 = _lt(
    0x62BD, 0x51FA, 0x3067, 0x304D, 0x306A, 0x3044, 0x305F, 0x3081, 0x5B50, 0x3092, 0x4F5C, 0x6210, 0x3057, 0x306A,
    0x3044, 0xFF08, 0x89AA, 0x306E, 0x0020, 0x0061, 0x006C, 0x006C, 0x006F, 0x0077, 0x005F, 0x0070, 0x0061, 0x0074,
    0x0068, 0x0073, 0x0020, 0x306F, 0x7D99, 0x627F, 0x3057, 0x306A, 0x3044, 0xFF09
)
_LT_28 = _lt(0x306A, 0x3057)
_LT_29 = _lt(0x9023, 0x756A, 0x89E3, 0x6C7A, 0x003A, 0x0020, 0x30C6, 0x30FC, 0x30D6, 0x30EB)
_LT_3 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x003A, 0x0020, 0x006D, 0x0069, 0x006C, 0x0065, 0x0073,
    0x0074, 0x006F, 0x006E, 0x0065, 0x0020, 0x3092, 0x81EA, 0x52D5, 0x4F5C, 0x6210, 0x3057, 0x307E, 0x3057, 0x305F,
    0x005C, 0x006E, 0x005C, 0x006E
)
_LT_30 = _lt(
    0x672A, 0x89E3, 0x6C7A, 0x306E, 0x524D, 0x65B9, 0x53C2, 0x7167, 0x003A, 0x0020, 0x30C6, 0x30FC, 0x30D6, 0x30EB
)
_LT_31 = _lt(
    0x9664, 0x5916, 0x3057, 0x305F, 0x0020, 0x0073, 0x0063, 0x006F, 0x0070, 0x0065, 0x003A, 0x006D, 0x0069, 0x006C,
    0x0065, 0x0073, 0x0074, 0x006F, 0x006E, 0x0065, 0x0020, 0x4F9D, 0x5B58, 0x003A, 0x0020, 0x0023
)
_LT_32 = _lt(
    0x0061, 0x006C, 0x006C, 0x006F, 0x0077, 0x005F, 0x0070, 0x0061, 0x0074, 0x0068, 0x0073, 0x0020, 0x0066, 0x006F,
    0x0072, 0x0020, 0x0052, 0x004F, 0x0057, 0x005F, 0x0052, 0x0045, 0x0050, 0x004F, 0x0020, 0x0066, 0x0072, 0x006F,
    0x006D, 0x0020, 0x0074, 0x0068, 0x0065, 0x0020, 0x0023, 0x0023, 0x0023, 0x0023, 0x0020, 0x30B5, 0x30D6, 0x004E,
    0x0020, 0x0063, 0x0068, 0x0061, 0x006E, 0x0067, 0x0065, 0x0020, 0x0074, 0x0061, 0x0062, 0x006C, 0x0065, 0x002E
)
_LT_33 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x30B5,
    0x30D6
)
_LT_34 = _lt(
    0x0020, 0x0062, 0x006F, 0x0064, 0x0079, 0x0020, 0x306B, 0x0020, 0x0043, 0x0050, 0x0031, 0x0020, 0x7981, 0x5247,
    0x8A9E, 0x304C, 0x6B8B, 0x5B58, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_35 = _lt(0x003A, 0x0020, 0x0043, 0x0050, 0x0031, 0x0020, 0x7981, 0x5247, 0x8A9E)
_LT_36 = _lt(0x0023, 0x0023, 0x0020, 0x4F9D, 0x5B58, 0xFF08, 0x5148, 0x884C, 0xFF09)
_LT_37 = _lt(
    0x007C, 0x0020, 0x0023, 0x0020, 0x007C, 0x0020, 0x4F9D, 0x5B58, 0x5148, 0x0020, 0x007C, 0x0020, 0x72B6, 0x614B,
    0x0020, 0x007C
)
_LT_38 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x0049,
    0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x4F5C, 0x6210, 0x5931, 0x6557, 0xFF08, 0x30B5, 0x30D6
)
_LT_39 = _lt(0xFF09, 0x005C, 0x006E, 0x005C, 0x006E)
_LT_4 = _lt(
    0x89AA, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x306B, 0x0020, 0x006D, 0x0069, 0x006C, 0x0065,
    0x0073, 0x0074, 0x006F, 0x006E, 0x0065, 0x0020, 0x304C, 0x672A, 0x8A2D, 0x5B9A, 0x3060, 0x3063, 0x305F, 0x305F,
    0x3081, 0x0020
)
_LT_40 = _lt(
    0x003E, 0x0020, 0x5916, 0x90E8, 0x30EA, 0x30DD, 0x30B8, 0x30C8, 0x30EA, 0x306E, 0x30EA, 0x30EA, 0x30FC, 0x30B9,
    0x3068, 0x0020, 0x0072, 0x0065, 0x006C, 0x0065, 0x0061, 0x0073, 0x0065, 0x002D, 0x0077, 0x0061, 0x0074, 0x0063,
    0x0068, 0x0065, 0x0072, 0x0020, 0x306E, 0x0020, 0x0062, 0x0075, 0x006D, 0x0070, 0x0020, 0x0049, 0x0073, 0x0073,
    0x0075, 0x0065, 0x0020, 0x304C, 0x0020
)
_LT_41 = _lt(
    0x006D, 0x0065, 0x0072, 0x0067, 0x0065, 0x002D, 0x0064, 0x006F, 0x006E, 0x0065, 0x0020, 0x306B, 0x306A, 0x3063,
    0x3066, 0x304B, 0x3089, 0x3053, 0x306E, 0x5B50, 0x3092, 0x6295, 0x5165, 0x3059, 0x308B
)
_LT_42 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x5B50,
    0x0020, 0x0023
)
_LT_43 = _lt(0x8A2D, 0x8A08)
_LT_44 = _lt(0x0023, 0x0023, 0x0023, 0x0023, 0x0020, 0x30B5, 0x30D6)
_LT_45 = _lt(0x30B9, 0x30B3, 0x30FC, 0x30D7)
_LT_46 = _lt(0x8A2D, 0x8A08, 0x65B9, 0x91DD)
_LT_47 = _lt(0x5909, 0x66F4, 0x5BFE, 0x8C61, 0x30D5, 0x30A1, 0x30A4, 0x30EB)
_LT_48 = _lt(0x53D7, 0x3051, 0x5165, 0x308C, 0x6761, 0x4EF6)
_LT_49 = _lt(0x65E2, 0x5B58, 0x003A)
_LT_5 = _lt(0x0060, 0xFF08, 0x0023)
_LT_50 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x5B50,
    0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x0062, 0x006F, 0x0064, 0x0079, 0x0020, 0x691C, 0x8A3C,
    0x5931, 0x6557, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_51 = _lt(
    0x4EE5, 0x4E0B, 0x306E, 0x884C, 0x3067, 0x0020, 0x0070, 0x0072, 0x0065, 0x002D, 0x0063, 0x0072, 0x0065, 0x0061,
    0x0074, 0x0069, 0x006F, 0x006E, 0x0020, 0x0076, 0x0061, 0x006C, 0x0069, 0x0064, 0x0061, 0x0074, 0x0069, 0x006F,
    0x006E, 0x0020, 0x0028, 0x0056, 0x0031, 0x2013, 0x0056, 0x0035, 0x0029, 0x0020, 0x304C, 0x5931, 0x6557, 0x3057,
    0x305F, 0x305F, 0x3081, 0x0020
)
_LT_52 = _lt(
    0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x3092, 0x4F5C, 0x6210, 0x3057, 0x307E, 0x305B, 0x3093, 0x3067,
    0x3057, 0x305F, 0x003A, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_53 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x8B66, 0x544A, 0x003A, 0x0020, 0x4E00, 0x90E8,
    0x884C, 0x306E, 0x691C, 0x8A3C, 0x5931, 0x6557, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_54 = _lt(0x89AA, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x003A, 0x0020, 0x0023)
_LT_55 = _lt(0x4F9D, 0x5B58, 0x003A, 0x0020)
_LT_56 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x4F5C,
    0x6210, 0x5F8C, 0x0020, 0x0076, 0x0061, 0x006C, 0x0069, 0x0064, 0x0061, 0x0074, 0x0065, 0x005F, 0x0063, 0x0068,
    0x0069, 0x006C, 0x0064, 0x0072, 0x0065, 0x006E, 0x0020, 0x5931, 0x6557, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_57 = _lt(0x002D, 0x0020, 0x0028, 0x306A, 0x3057, 0x0029)
_LT_58 = _lt(0x0023, 0x0023, 0x0020, 0x30B9, 0x30B3, 0x30FC, 0x30D7)
_LT_59 = _lt(0x0023, 0x0023, 0x0020, 0x8A2D, 0x8A08)
_LT_6 = _lt(
    0xFF09, 0x3092, 0x4F5C, 0x6210, 0x3057, 0x3066, 0x7D10, 0x4ED8, 0x3051, 0x307E, 0x3057, 0x305F, 0x3002, 0x005C,
    0x006E
)
_LT_60 = _lt(0x003E, 0x0020, 0x89AA, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x0020, 0x0023)
_LT_61 = _lt(0x0020, 0x30B5, 0x30D6)
_LT_62 = _lt(0x0020, 0x304B, 0x3089, 0x5C0E, 0x51FA)
_LT_63 = _lt(
    0x306A, 0x3057, 0xFF08, 0x30B5, 0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x30EA, 0x30F3, 0x30AF, 0x306E, 0x307F,
    0xFF09
)
_LT_64 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30B5, 0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC,
    0x4F5C, 0x6210, 0x5B8C, 0x4E86, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_65 = _lt(0x4F5C, 0x6210, 0x3057, 0x305F, 0x30B5, 0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x003A, 0x005C, 0x006E)
_LT_66 = _lt(0x0023, 0x0023, 0x0020, 0x5909, 0x66F4, 0x5BFE, 0x8C61, 0x30D5, 0x30A1, 0x30A4, 0x30EB)
_LT_67 = _lt(
    0x691C, 0x8A3C, 0x6E08, 0x307F, 0x306E, 0x5B50, 0x0020, 0x0064, 0x0065, 0x0076, 0x0065, 0x006C, 0x006F, 0x0070,
    0x0020, 0x0072, 0x0065, 0x0071, 0x0075, 0x0065, 0x0073, 0x0074, 0x0020, 0x306F, 0x0020, 0x0071, 0x0075, 0x0065,
    0x0075, 0x0065, 0x0020, 0x304C, 0x4F9D, 0x5B58, 0x9806, 0x306B, 0x81EA, 0x52D5, 0x6295, 0x5165, 0x3059, 0x308B,
    0x3002, 0x005C, 0x006E
)
_LT_68 = _lt(0x0023, 0x0023, 0x0020, 0x53D7, 0x3051, 0x5165, 0x308C, 0x6761, 0x4EF6)
_LT_69 = _lt(
    0x5F71, 0x97FF, 0x7BC4, 0x56F2, 0x8ABF, 0x67FB, 0xFF08, 0x0073, 0x0063, 0x006F, 0x0070, 0x0065, 0x003A, 0x006D,
    0x0069, 0x0067, 0x0072, 0x0061, 0x0074, 0x0069, 0x006F, 0x006E, 0x0020, 0x6642, 0x306F, 0x5FC5, 0x9808, 0xFF09
)
_LT_7 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x8B66, 0x544A, 0x003A, 0x0020, 0x006D, 0x0069,
    0x006C, 0x0065, 0x0073, 0x0074, 0x006F, 0x006E, 0x0065, 0x0020, 0x81EA, 0x52D5, 0x4F5C, 0x6210, 0x306B, 0x5931,
    0x6557, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_70 = _lt(
    0x0023, 0x0023, 0x0020, 0x5F71, 0x97FF, 0x7BC4, 0x56F2, 0x8ABF, 0x67FB, 0xFF08, 0x0073, 0x0063, 0x006F, 0x0070,
    0x0065, 0x003A, 0x006D, 0x0069, 0x0067, 0x0072, 0x0061, 0x0074, 0x0069, 0x006F, 0x006E, 0x0020, 0x6642, 0x306F,
    0x5FC5, 0x9808, 0xFF09
)
_LT_71 = _lt(0x7834, 0x58CA, 0x7684, 0x5909, 0x66F4, 0x306E, 0x5F71, 0x97FF, 0x7BC4, 0x56F2)
_LT_72 = _lt(0x0023, 0x0023, 0x0020, 0x7834, 0x58CA, 0x7684, 0x5909, 0x66F4, 0x306E, 0x5F71, 0x97FF, 0x7BC4, 0x56F2)
_LT_73 = _lt(0x30D5, 0x30A1, 0x30A4, 0x30EB, 0x30D1, 0x30B9)
_LT_74 = _lt(
    0x0056, 0x0032, 0x003A, 0x0020, 0x0061, 0x006C, 0x006C, 0x006F, 0x0077, 0x005F, 0x0070, 0x0061, 0x0074, 0x0068,
    0x0073, 0x0020, 0x672A, 0x5305, 0x542B, 0x0020, 0x0028
)
_LT_75 = _lt(
    0x0056, 0x0034, 0x003A, 0x0020, 0x4F9D, 0x5B58, 0x3042, 0x308A, 0x3060, 0x304C, 0x0020, 0x0023, 0x0023, 0x0020,
    0x4F9D, 0x5B58, 0xFF08, 0x5148, 0x884C, 0xFF09, 0x0020, 0x30BB, 0x30AF, 0x30B7, 0x30E7, 0x30F3, 0x6B20, 0x843D
)
_LT_76 = _lt(
    0x0056, 0x0035, 0x003A, 0x0020, 0x4F9D, 0x5B58, 0x304C, 0x89AA, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065,
    0x0020, 0x81EA, 0x8EAB, 0x3092, 0x53C2, 0x7167, 0x0020, 0x0028, 0x0023
)
_LT_77 = _lt(
    0x0056, 0x0035, 0x003A, 0x0020, 0x4F9D, 0x5B58, 0x304C, 0x0020, 0x0073, 0x0063, 0x006F, 0x0070, 0x0065, 0x003A,
    0x006D, 0x0069, 0x006C, 0x0065, 0x0073, 0x0074, 0x006F, 0x006E, 0x0065, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075,
    0x0065, 0x0020, 0x3092, 0x53C2, 0x7167, 0x0020, 0x0028, 0x0023
)
_LT_78 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x0060,
    0x0023, 0x0023, 0x0020, 0x8A2D, 0x8A08, 0x0060, 0x0020, 0x30BB, 0x30AF, 0x30B7, 0x30E7, 0x30F3, 0x304C, 0x91CD,
    0x8907, 0x3057, 0x3066, 0x3044, 0x307E, 0x3059
)
_LT_79 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x8A2D,
    0x8A08, 0x307E, 0x305F, 0x306F, 0x53D7, 0x3051, 0x5165, 0x308C, 0x6761, 0x4EF6, 0x304C, 0x672A, 0x8A18, 0x8F09,
    0x005C, 0x006E, 0x005C, 0x006E
)
_LT_8 = _lt(
    0x89AA, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x306B, 0x0020, 0x006D, 0x0069, 0x006C, 0x0065,
    0x0073, 0x0074, 0x006F, 0x006E, 0x0065, 0x0020, 0x304C, 0x672A, 0x8A2D, 0x5B9A, 0x3067, 0x3001, 0x81EA, 0x52D5,
    0x4F5C, 0x6210, 0x3082, 0x5931, 0x6557, 0x3057, 0x307E, 0x3057, 0x305F, 0xFF08
)
_LT_80 = _lt(
    0x89AA, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x306B, 0x0020, 0x0060, 0x0023, 0x0023, 0x0020, 0x8A2D, 0x8A08, 0x0060,
    0x0020, 0x3068, 0x0020, 0x0060, 0x0023, 0x0023, 0x0020, 0x53D7, 0x3051, 0x5165, 0x308C, 0x6761, 0x4EF6, 0x0060,
    0x0020, 0x306E, 0x4E21, 0x65B9, 0x304C, 0x5FC5, 0x8981, 0x3067, 0x3059, 0x3002
)
_LT_81 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x30B5,
    0x30D6, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x8A73, 0x7D30, 0x8A2D, 0x8A08, 0x304C, 0x672A, 0x751F, 0x6210, 0xFF08,
    0x0042, 0x0031, 0x0020, 0x672A, 0x5B8C, 0x4E86, 0x306E, 0x53EF, 0x80FD, 0x6027, 0xFF09, 0x005C, 0x006E, 0x005C,
    0x006E
)
_LT_82 = _lt(
    0x89AA, 0x30A4, 0x30B7, 0x30E5, 0x30FC, 0x306E, 0x0020, 0x0060, 0x0023, 0x0023, 0x0020, 0x8A2D, 0x8A08, 0x0060,
    0x0020, 0x306B, 0x0020, 0x0060, 0x0023, 0x0023, 0x0023, 0x0023, 0x0020, 0x30B5, 0x30D6, 0x004E, 0x0060, 0x0020,
    0x30B5, 0x30D6, 0x30BB, 0x30AF, 0x30B7, 0x30E7, 0x30F3, 0x304C, 0x3042, 0x308A, 0x307E, 0x305B, 0x3093, 0x3002
)
_LT_83 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x0073,
    0x0063, 0x006F, 0x0070, 0x0065, 0x003A, 0x006D, 0x0069, 0x006C, 0x0065, 0x0073, 0x0074, 0x006F, 0x006E, 0x0065,
    0x0020, 0x306A, 0x3057, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_84 = _lt(
    0x0060, 0x0069, 0x0073, 0x0073, 0x0075, 0x0065, 0x0073, 0x006D, 0x0069, 0x0074, 0x0068, 0x003A, 0x0073, 0x0075,
    0x0062, 0x002D, 0x0072, 0x0065, 0x0061, 0x0064, 0x0079, 0x0060, 0x0020, 0x306F, 0x0020, 0x0060, 0x0073, 0x0063,
    0x006F, 0x0070, 0x0065, 0x003A, 0x006D, 0x0069, 0x006C, 0x0065, 0x0073, 0x0074, 0x006F, 0x006E, 0x0065, 0x0060,
    0x0020, 0x30E9, 0x30D9, 0x30EB, 0x4ED8, 0x304D, 0x0020, 0x0049, 0x0073, 0x0073, 0x0075, 0x0065, 0x0020, 0x306B,
    0x306E, 0x307F, 0x4F7F, 0x7528, 0x3067, 0x304D, 0x307E, 0x3059, 0x3002
)
_LT_85 = _lt(
    0x0023, 0x0023, 0x0020, 0x0053, 0x0055, 0x0042, 0x0031, 0x0020, 0x30A8, 0x30E9, 0x30FC, 0x003A, 0x0020, 0x0043,
    0x0050, 0x0031, 0x0020, 0x672A, 0x5B8C, 0x4E86, 0x005C, 0x006E, 0x005C, 0x006E
)
_LT_86 = _lt(
    0x0042, 0x0031, 0x0020, 0x30D6, 0x30E9, 0x30C3, 0x30B7, 0x30E5, 0x30A2, 0x30C3, 0x30D7, 0x5F8C, 0x306E, 0x0020,
    0x0043, 0x0050, 0x0031, 0x0020, 0x30B2, 0x30FC, 0x30C8, 0x304C, 0x307E, 0x3060, 0x5B8C, 0x4E86, 0x3057, 0x3066,
    0x3044, 0x307E, 0x305B, 0x3093, 0x3002
)
_LT_9 = _lt(0xFF09, 0x3002)


@dataclass
class PlanRow:
    row_num: int
    title: str
    repo: str
    scope: str
    dep_raw: str


@dataclass
class Sub1State:
    resolved_logs: list[str] = field(default_factory=list)
    excluded_milestone_logs: list[str] = field(default_factory=list)
    unresolved_forward_logs: list[str] = field(default_factory=list)
    validation_failures: list[str] = field(default_factory=list)
    created_issues: list[str] = field(default_factory=list)
    row_to_issue: dict[int, int] = field(default_factory=dict)
    created_children: list[dict[str, Any]] = field(default_factory=list)


def _sub1_safe_metadata(body: str) -> dict[str, Any]:
    try:
        return parse_issue_metadata(body)
    except ValueError:
        return parse_frontmatter_fields(body) or {}


def parse_split_plan(
    body: str, *, parent_target_repo: str
) -> tuple[list[PlanRow], bool]:
    """Parse the configured split-plan table by header names (column order not fixed)."""
    section = _plan_section(body)
    if section is None:
        return [], False
    rows = _parse_table_rows(section)
    if len(rows) <= 1:
        return [], False
    header = [cell.strip().strip("`") for cell in rows[0]]

    def _idx(*names: str, exact: bool = False) -> int | None:
        for name in names:
            for i, cell in enumerate(header):
                if cell == name or (not exact and name in cell):
                    return i
        return None

    num_i = _idx("#", exact=True)
    title_i = _idx(_LT_15)
    repo_i = _idx(_LT_16)
    scope_i = _idx(_LT_17)
    dep_i = _idx(_LT_18)
    if num_i is None or title_i is None:
        return [], repo_i is not None

    has_repo = repo_i is not None
    plan_rows: list[PlanRow] = []
    for row in rows[1:]:
        if len(row) <= max(num_i, title_i):
            continue
        num_raw = row[num_i].strip()
        if not num_raw.isdigit():
            continue
        title = row[title_i].strip()
        scope = row[scope_i].strip() if scope_i is not None and len(row) > scope_i else ""
        dep_raw = row[dep_i].strip() if dep_i is not None and len(row) > dep_i else ""
        if has_repo and repo_i is not None and len(row) > repo_i:
            repo = row[repo_i].strip().strip("`")
        else:
            repo = parent_target_repo
        plan_rows.append(
            PlanRow(
                row_num=int(num_raw),
                title=title,
                repo=repo,
                scope=scope,
                dep_raw=dep_raw,
            )
        )
    plan_rows.sort(key=lambda r: r.row_num)
    return plan_rows, has_repo


def resolve_dependencies(
    dep_raw: str,
    *,
    table_row_count: int,
    row_to_issue: dict[int, int],
    client: ForgePort,
    state: Sub1State,
) -> str:
    """Resolve plan-row dependency tokens to concrete Issue numbers."""
    dep = (dep_raw or "").strip()
    if not dep or dep == _LT_28:
        return _LT_28
    seen: list[int] = []
    labels_cache: dict[int, list[str]] = {}

    def _repl(match: re.Match[str]) -> str:
        k = int(match.group(1))
        first = k not in seen
        if first:
            seen.append(k)
        if k <= table_row_count:
            if k in row_to_issue:
                target = f"#{row_to_issue[k]}"
                if first:
                    state.resolved_logs.append(f"{_LT_29}{k} → {target}")
                return target
            if first:
                state.unresolved_forward_logs.append(f"{_LT_30}{k}")
            return match.group(0)
        if k not in labels_cache:
            try:
                issue = client.issue_get(k, fields=["labels"])
                labels_cache[k] = sorted(label_names(issue))
            except Exception:
                labels_cache[k] = []
        if "scope:milestone" in labels_cache[k]:
            if first:
                state.excluded_milestone_logs.append(f"{_LT_31}{k}")
            return ""
        return match.group(0)

    resolved = _SUB1_DEP_REF_RE.sub(_repl, dep)
    resolved = re.sub(r"\s+", " ", resolved).strip(" ,;|")
    if not resolved or resolved == _LT_28:
        return _LT_28
    return resolved


def allow_paths_for_row(parent_body: str, row: PlanRow) -> list[str]:
    _LT_32
    sub_body = sub_block(parent_body, row.row_num)
    if not sub_body:
        return []
    paths: list[str] = []
    for path in change_paths_for_repo(sub_body, row.repo):
        if path.startswith("/var/tmp/"):
            print(f"WARN: skip invalid allow_path {path!r}", file=sys.stderr)
            continue
        if path.startswith(_SUB1_NIKKI_PREFIX):
            continue
        paths.append(path)
    return paths


def _sub1_build_dep_section(
    resolved_dep: str, *, row_repo: str, client: ForgePort
) -> str:
    if not resolved_dep or resolved_dep == _LT_28:
        return ""
    nums = [int(m.group(1)) for m in _SUB1_DEP_REF_RE.finditer(resolved_dep)]
    if not nums:
        return ""
    lines = [
        _LT_36,
        "",
        _LT_37,
        "|---|--------|------|",
    ]
    cross_repo = False
    for idx, num in enumerate(nums, start=1):
        try:
            data = client.issue_get(num, fields=["title", "state", "body"])
        except Exception:
            data = {"title": "?", "state": "UNKNOWN", "body": ""}
        title = str(data.get("title") or "?")
        state = str(data.get("state") or "?")
        dep_repo = str(_sub1_safe_metadata(str(data.get("body") or "")).get("target_repo") or "")
        if row_repo == "sumipan/nexus" and dep_repo and dep_repo != "sumipan/nexus":
            cross_repo = True
        lines.append(f"| {idx} | #{num} ({title}) | {state} |")
    if cross_repo:
        lines.append("")
        lines.append(
            _LT_40 +
            _LT_41
        )
    return "\n".join(lines) + "\n"


def _sub1_section_parts(parent_body: str, row_num: int) -> dict[str, str]:
    sub_secs = get_subsections(parent_body, _LT_43, _LT_44)
    prefix = f"{_LT_44}{row_num}:"
    for heading, content in sub_secs:
        if heading.startswith(prefix):
            return {
                "scope": (get_section(content, _LT_45) or "").strip(),
                "design": (get_section(content, _LT_46) or "").strip(),
                "files": (get_section(content, _LT_47) or "").strip(),
                "ac": (get_section(content, _LT_48) or "").strip(),
            }
    return {}


def build_child_body(
    *,
    parent_body: str,
    parent_number: int,
    row: PlanRow,
    resolved_dep: str,
    client: ForgePort,
    parent_labels: list[str],
    allow_paths: list[str] | None = None,
) -> str:
    """Build a child Issue body from parent design and a split-plan row."""
    parent_meta = _sub1_safe_metadata(parent_body)
    if allow_paths is None:
        allow_paths = allow_paths_for_row(parent_body, row)
    yaml_lines = [f"target_repo: {row.repo}"]
    base = parent_meta.get("base_branch")
    if isinstance(base, str) and base.strip():
        yaml_lines.append(f"base_branch: {base.strip()}")
    if allow_paths:
        yaml_lines.append("allow_paths:")
        for path in allow_paths:
            yaml_lines.append(f'  - "{path}"')
    diary_allow = parent_meta.get("diary_allow_paths")
    if isinstance(diary_allow, list) and diary_allow:
        yaml_lines.append("diary_allow_paths:")
        for path in diary_allow:
            if isinstance(path, str):
                yaml_lines.append(f'  - "{path}"')

    parts = [
        "```yaml",
        *yaml_lines,
        "```",
        "",
        f"{_LT_54}{parent_number}",
        f"{_LT_55}{resolved_dep}",
        "",
    ]
    dep_section = _sub1_build_dep_section(resolved_dep, row_repo=row.repo, client=client)
    if dep_section:
        parts.append(dep_section)

    sub = _sub1_section_parts(parent_body, row.row_num)
    if sub:
        parts.append(_LT_58)
        parts.append(sub.get("scope") or row.scope or "")
        parts.append("")
        parts.append(_LT_59)
        parts.append(f"{_LT_60}{parent_number}{_LT_61}{row.row_num}{_LT_62}")
        parts.append("")
        if sub.get("design"):
            parts.append(sub["design"])
            parts.append("")
        if sub.get("files"):
            parts.append(_LT_66)
            parts.append(sub["files"])
            parts.append("")
        parts.append(_LT_68)
        parts.append(sub.get("ac") or "- [ ] (from parent)")
        parts.append("")
    else:
        parts.append(_LT_58)
        parts.append(row.scope or "")
        parts.append("")
        design = get_section(parent_body, _LT_43) or ""
        parts.append(_LT_59)
        parts.append(f"{_LT_60}{parent_number}{_LT_61}{row.row_num}{_LT_62}")
        parts.append("")
        parts.append(design.strip())
        parts.append("")
        ac = get_section(parent_body, _LT_48) or get_section_by_keyword(
            parent_body, _LT_48
        )
        parts.append(_LT_68)
        parts.append((ac or "").strip())
        parts.append("")

    if "scope:migration" in parent_labels:
        filtered = _sub1_filter_parent_section(
            parent_body, row.row_num, _LT_69
        )
        if filtered:
            parts.append(_LT_70)
            parts.append(filtered)
            parts.append("")
    breaking = _sub1_filter_parent_section(parent_body, row.row_num, _LT_71)
    if breaking:
        parts.append(_LT_72)
        parts.append(breaking)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _sub1_filter_parent_section(parent_body: str, row_num: int, heading: str) -> str:
    sub_secs = get_subsections(parent_body, _LT_43, _LT_44)
    sub_body = ""
    prefix = f"{_LT_44}{row_num}:"
    for h, content in sub_secs:
        if h.startswith(prefix):
            sub_body = content
            break
    sub_paths: list[str] = []
    tbl = get_section(sub_body, _LT_47) if sub_body else None
    if tbl:
        for line in tbl.splitlines():
            if not line.startswith("|") or "---|" in line:
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            cell = parts[1].strip().strip("`")
            last = cell.rfind(":")
            if last >= 0 and cell[last + 1 :].isdigit():
                cell = cell[:last]
            if cell and cell not in (_LT_73,):
                sub_paths.append(cell)
    section_content = get_section(parent_body, heading)
    if not section_content or not sub_paths:
        return ""
    result = filter_section_by_paths(section_content, sub_paths)
    return "\n".join(result) if result else ""


def prevalidate_child_body(
    *,
    body: str,
    row_repo: str,
    parent_issue_number: int,
    resolved_dep: str,
    client: ForgePort,
    supported: frozenset[str] | set[str],
) -> list[str]:
    """Pre-creation V1–V5. V1–V3 via shared milestone helpers."""
    meta = _sub1_safe_metadata(body)
    child_repo = str(meta.get("target_repo") or "").strip() or None
    failures = check_v1_target_repo(child_repo, row_repo, supported)

    allow_paths_raw = meta.get("allow_paths") or []
    allow_paths = [str(p) for p in allow_paths_raw if isinstance(p, str)]
    paths = change_paths_for_repo(body, child_repo or row_repo)
    v2 = check_v2_allow_paths(allow_paths, paths)
    for item in v2:
        if item.startswith("V2 allow_paths missing:"):
            missing = item.split(":", 1)[1].strip()
            for path in [p.strip() for p in missing.split(",") if p.strip()]:
                failures.append(f"{_LT_74}{path})")
        else:
            failures.append(item)

    failures.extend(check_v3_cjk_placeholders(allow_paths=allow_paths))

    dep = (resolved_dep or "").strip()
    if dep and dep != _LT_28:
        if _LT_36 not in body:
            failures.append(_LT_75)
        for num_str in _SUB1_DEP_REF_RE.findall(dep):
            num = int(num_str)
            if num == parent_issue_number:
                failures.append(f"{_LT_76}{num})")
                continue
            try:
                labels = sorted(label_names(client.issue_get(num, fields=["labels"])))
            except Exception:
                labels = []
            if "scope:milestone" in labels:
                failures.append(f"{_LT_77}{num})")
    return failures


def _sub1_check_cp1_gate(comments: list[dict[str, Any]]) -> str:
    def _ts(comment: dict[str, Any]) -> datetime:
        raw = str(comment.get("createdAt") or comment.get("created_at") or "")
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))

    cp1_comments = [c for c in comments if "CP1_STATUS:" in str(c.get("body") or "")]
    brushup = [
        c for c in comments if "PIPELINE_STATUS: BRUSHUP_DONE" in str(c.get("body") or "")
    ]
    if not brushup:
        return "PASS"
    latest_brushup = max(_ts(c) for c in brushup)
    fresh = [c for c in cp1_comments if _ts(c) > latest_brushup]
    if not fresh:
        return "CP1_NOT_READY"
    latest = sorted(fresh, key=_ts)[-1]
    body = str(latest.get("body") or "")
    if "CP1_STATUS: FAIL" in body and "INTENTIONAL_HOLD: true" not in body:
        return "BLOCK"
    return "PASS"


def _sub1_parent_design_gate(body: str) -> str | None:
    if count_heading(body, _LT_43) > 1:
        return _LT_78
    design = get_section(body, _LT_43)
    ac = get_section(body, _LT_48) or get_section_by_keyword(body, _LT_48)
    if not design or not design.strip() or not ac or not ac.strip():
        return (
            _LT_79 +
            _LT_80
        )
    if not get_subsections(body, _LT_43, _LT_44):
        return (
            _LT_81 +
            _LT_82
        )
    return None


def resolve_sub1_template(step: StepConfig | None) -> str | None:
    if step is not None and step.template:
        return step.template
    cfg_step = get_config().steps.get("sub1")
    if cfg_step is not None and cfg_step.template:
        return cfg_step.template
    template_dir = get_config().paths.template_dir
    candidate = template_dir / _DEFAULT_SUB1_TEMPLATE
    if candidate.is_file():
        return _DEFAULT_SUB1_TEMPLATE
    return None


def run_guarded_sub1_body(
    ctx: StepContext,
    *,
    row: PlanRow,
    body_path: Path,
    template_name: str,
) -> int:
    from issuesmith.engine import resolve, run_guarded

    template = str(get_config().paths.template_dir / template_name)
    selection = resolve("implementation")
    variables = [
        f"issue_number={ctx.issue_number}",
        f"row_num={row.row_num}",
        f"row_title={row.title}",
        f"row_repo={row.repo}",
        f"sub_body_path={body_path}",
        f"target_repo={ctx.target_repo}",
        f"model={selection.model}",
        f"execution_constraints={ctx.execution_constraints}",
    ]
    return run_guarded(
        "implementation",
        template,
        variables,
        success_statuses=["SUB_BODY_READY", "SUB_CREATED"],
        failure_status="SUB_BODY_FAILED",
    )


def _sub1_fail(
    client: ForgePort, issue_number: int, comment: str, status: str = "IMPL_FAILED"
) -> StepResult:
    if "PIPELINE_STATUS:" not in comment:
        comment = comment.rstrip() + f"\n\nPIPELINE_STATUS: {status}\n"
    try:
        client.issue_comment(issue_number, comment)
    except Exception as exc:
        print(f"WARN: comment failed: {exc}", file=sys.stderr)
    return StepResult(exit_code=1, pipeline_status=status)


def run_sub1_create(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute SUB1: parse plan, pre-validate, create children, post-validate."""
    issue_number = int(ctx.issue_number)
    client = get_forge()
    parent = client.issue_get(
        issue_number,
        fields=["number", "body", "labels", "milestone", "comments", "title"],
    )
    parent_body = str(parent.get("body") or "")
    labels = sorted(label_names(parent))
    comments = parent.get("comments") or []
    if not isinstance(comments, list):
        comments = []

    if "scope:milestone" not in labels:
        return _sub1_fail(
            client,
            issue_number,
            _LT_83 +
            _LT_84,
        )

    cp1 = _sub1_check_cp1_gate([c for c in comments if isinstance(c, dict)])
    if cp1 == "CP1_NOT_READY":
        return _sub1_fail(
            client,
            issue_number,
            _LT_85 +
            _LT_86,
        )
    if cp1 == "BLOCK":
        return _sub1_fail(
            client,
            issue_number,
            _LT_1 +
            _LT_2,
        )

    milestone_number = _milestone_number(parent)
    if milestone_number is None:
        from issuesmith.convert_to_milestone import _ensure_milestone

        try:
            created_title = _ensure_milestone(client, issue_number, dry_run=False)
            parent = client.issue_get(
                issue_number,
                fields=["number", "body", "labels", "milestone", "comments", "title"],
            )
            milestone_number = _milestone_number(parent)
            client.issue_comment(
                issue_number,
                _LT_3 +
                f"{_LT_4}"
                f"`{created_title}{_LT_5}{milestone_number}{_LT_6}",
            )
        except Exception as exc:
            try:
                client.issue_comment(
                    issue_number,
                    _LT_7 +
                    f"{_LT_8}{exc}{_LT_9}" +
                    _LT_10,
                )
            except Exception:
                pass

    design_err = _sub1_parent_design_gate(parent_body)
    if design_err:
        return _sub1_fail(client, issue_number, design_err)

    parent_meta = _sub1_safe_metadata(parent_body)
    parent_target_repo = str(parent_meta.get("target_repo") or "").strip()
    plan_rows, has_repo_col = parse_split_plan(
        parent_body, parent_target_repo=parent_target_repo
    )
    if not plan_rows:
        return _sub1_fail(
            client,
            issue_number,
            _LT_11,
        )
    if not has_repo_col and not parent_target_repo:
        return _sub1_fail(
            client,
            issue_number,
            _LT_12 +
            f"{_LT_13}{issue_number}{_LT_14}",
        )

    supported = get_config().supported_repos
    existing: dict[str, int] = {}
    for item in _list_chain_children(client, issue_number, parent):
        title = str(item.get("title") or "")
        number = item.get("number")
        if title and isinstance(number, int):
            existing[title] = number

    state = Sub1State()
    table_row_count = len(plan_rows)
    template_name = resolve_sub1_template(step)
    skip_count = 0

    for row in plan_rows:
        if has_repo_col and not row.repo:
            state.validation_failures.append(
                f"{_LT_19}{row.row_num}{_LT_20}"
            )
            continue

        if row.title in existing:
            child_num = existing[row.title]
            state.row_to_issue[row.row_num] = child_num
            if not ensure_sub1_binding(client, issue_number, child_num):
                return _sub1_fail(
                    client,
                    issue_number,
                    f"{_LT_21}{child_num}{_LT_22}",
                )
            state.created_issues.append(f"{_LT_23}{child_num} / {row.title}{_LT_24}")
            continue

        resolved_dep = resolve_dependencies(
            row.dep_raw,
            table_row_count=table_row_count,
            row_to_issue=state.row_to_issue,
            client=client,
            state=state,
        )
        row_allow_paths = allow_paths_for_row(parent_body, row)
        if not row_allow_paths:
            state.validation_failures.append(
                f"{_LT_19}{row.row_num}{_LT_25}{row.repo}{_LT_26}" +
                _LT_27
            )
            continue
        body = build_child_body(
            parent_body=parent_body,
            parent_number=issue_number,
            row=row,
            resolved_dep=resolved_dep,
            client=client,
            parent_labels=labels,
            allow_paths=row_allow_paths,
        )

        if template_name:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(body)
                tmp_path = Path(tmp.name)
            try:
                rc = run_guarded_sub1_body(
                    ctx, row=row, body_path=tmp_path, template_name=template_name
                )
                if rc == 0 and tmp_path.is_file():
                    generated = tmp_path.read_text(encoding="utf-8")
                    if generated.strip():
                        body = generated
            except (ValueError, KeyError) as exc:
                print(
                    f"PIPELINE_STATUS: SUB1_BODY_INIT_ERROR\n{exc}",
                    file=sys.stderr,
                )
                sys.exit(1)
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                print(f"WARN: run-guarded body skipped: {exc}", file=sys.stderr)
                skip_count += 1
            finally:
                tmp_path.unlink(missing_ok=True)

        from issuesmith.gate_rules.cp1 import Cp1Rules

        cp1_hits = [
            v
            for v in Cp1Rules().check(body, [])
            if getattr(v, "rule_id", "") != "cp1.intentional_hold"
        ]
        if cp1_hits:
            detail = "\n".join(f"{v.rule_id}: {v.message}" for v in cp1_hits)
            try:
                client.issue_comment(
                    issue_number,
                    f"{_LT_33}{row.row_num}{_LT_34}"
                    f"{detail}\n\nPIPELINE_STATUS: SUB1_CP1_BLOCKED\n",
                )
            except Exception:
                pass
            state.validation_failures.append(f"{_LT_19}{row.row_num}{_LT_35}")
            continue

        pre_fail = prevalidate_child_body(
            body=body,
            row_repo=row.repo,
            parent_issue_number=issue_number,
            resolved_dep=resolved_dep,
            client=client,
            supported=supported,
        )
        if pre_fail:
            state.validation_failures.append(
                f"{_LT_19}{row.row_num}:\n" + "\n".join(pre_fail)
            )
            continue

        scope_labels = [
            lab for lab in labels if lab.startswith("scope:") and lab != "scope:milestone"
        ]
        create_labels = ["issuesmith:draft-done", *scope_labels]
        try:
            new_number = int(
                client.issue_create(
                    row.title, body, labels=create_labels, milestone=milestone_number
                )
            )
        except Exception as exc:
            return _sub1_fail(
                client,
                issue_number,
                f"{_LT_38}{row.row_num}{_LT_39}{exc}",
            )

        try:
            client.issue_update(
                new_number, labels_add=["issuesmith:draft-done", *scope_labels]
            )
        except Exception as exc:
            print(f"WARN: label update failed for #{new_number}: {exc}", file=sys.stderr)

        if not ensure_sub1_binding(client, issue_number, new_number):
            return _sub1_fail(
                client,
                issue_number,
                f"{_LT_42}{new_number}{_LT_22}",
            )

        state.row_to_issue[row.row_num] = new_number
        state.created_issues.append(f"#{new_number} / {row.title} / {row.repo}")
        state.created_children.append(
            {
                "number": new_number,
                "title": row.title,
                "body": body,
                "milestone": {"number": milestone_number} if milestone_number else None,
                "labels": [{"name": lab} for lab in create_labels],
            }
        )

    if template_name and skip_count == table_row_count and table_row_count >= 1:
        print("PIPELINE_STATUS: SUB1_BODY_INIT_ERROR", file=sys.stderr)
        sys.exit(1)

    if state.validation_failures and not state.created_children and not any(
        s.startswith(_LT_49) for s in state.created_issues
    ):
        fail_body = "\n".join(f"- {entry}" for entry in state.validation_failures)
        return _sub1_fail(
            client,
            issue_number,
            _LT_50 +
            _LT_51 +
            _LT_52 +
            f"{fail_body}\n",
        )

    if state.validation_failures:
        fail_body = "\n".join(f"- {entry}" for entry in state.validation_failures)
        try:
            client.issue_comment(
                issue_number,
                _LT_53 + fail_body + "\n",
            )
        except Exception:
            pass

    if state.created_children:
        post = validate_children(parent, state.created_children, client=client)
        if not post.passed:
            details = []
            for child in post.results:
                if not child.passed:
                    details.append(f"#{child.issue}: " + "; ".join(child.failures))
            return _sub1_fail(
                client,
                issue_number,
                _LT_56 + "\n".join(details),
            )

    created_block = "\n".join(f"- {line}" for line in state.created_issues) or _LT_57
    extra_logs = []
    extra_logs.extend(state.resolved_logs)
    extra_logs.extend(state.excluded_milestone_logs)
    extra_logs.extend(state.unresolved_forward_logs)
    log_block = ("\n" + "\n".join(extra_logs) + "\n") if extra_logs else "\n"
    ms = str(milestone_number) if milestone_number is not None else _LT_63
    try:
        client.issue_comment(
            issue_number,
            _LT_64 +
            f"{_LT_65}{created_block}\n\n"
            f"milestone: {ms}\n"
            f"{log_block}\n" +
            _LT_67,
        )
    except Exception as exc:
        print(f"WARN: completion comment failed: {exc}", file=sys.stderr)

    if not state.created_issues:
        return StepResult(exit_code=1, pipeline_status="IMPL_FAILED")
    return StepResult(exit_code=0, pipeline_status="SUB_CREATED")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(
            "usage: issuesmith milestone status <parent> | resume <parent> | prune [--dry-run]"
        )
        return 0 if args else 1
    cmd, *rest = args
    if cmd == "status":
        if not rest:
            print("error: parent issue number required", file=sys.stderr)
            return 2
        return milestone_status(int(rest[0]))
    if cmd == "resume":
        if not rest:
            print("error: parent issue number required", file=sys.stderr)
            return 2
        return milestone_resume(int(rest[0]))
    if cmd == "prune":
        dry_run = False
        for arg in rest:
            if arg == "--dry-run":
                dry_run = True
            else:
                print(f"Unknown prune option: {arg}", file=sys.stderr)
                return 2
        return milestone_prune(dry_run=dry_run)
    print(f"Unknown milestone command: {cmd}", file=sys.stderr)
    return 2
