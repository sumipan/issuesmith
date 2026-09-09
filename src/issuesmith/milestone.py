"""Milestone chain automation — sub phase enqueue and child develop orchestration."""

from __future__ import annotations

import fnmatch
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ghdag.github_client import GitHubClient

from issuesmith.config import IssuesmithConfig, MilestoneChainConfig, get_config
from issuesmith.dep_extractor import check_dependencies, extract_dependencies
from issuesmith.queue_store import QueueSnapshot, QueueStore
from issuesmith.queue_triage import (
    DONE_LABEL,
    READY_LABEL,
    RUNNING_LABEL,
    TERMINAL_WITHOUT_MERGE,
    label_names,
    parse_frontmatter_fields,
)

_MILESTONE_LABEL = "scope:milestone"
_SUB_LABELS = frozenset(
    {
        "issuesmith:sub-ready",
        "issuesmith:sub-running",
        "issuesmith:sub-done",
    }
)
_MILESTONE_IDLE_OK_LABELS = frozenset(
    {
        "issuesmith:draft-done",
        "issuesmith:sub-done",
        "issuesmith:sub-running",
    }
)
_CJK_PLACEHOLDER_RE = re.compile(
    r"(プレースホルダ|プレースホルダー|未記入|TBD|TODO|FIXME|XXX|ＸＸＸ|要記入|ここに)"
)
_TABLE_ROW_RE = re.compile(r"^\|")
_TABLE_SEPARATOR_RE = re.compile(r"^\|[\s\-:|]+\|$")
_CHANGE_TABLE_HEADER = re.compile(r"\*\*変更対象ファイル\*\*")
_PLAN_REF_RE = re.compile(r"^\|\s*(\d+)\s*\|")


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


def _issue_is_terminal(client: GitHubClient, issue_number: int) -> bool:
    return _classify_child_terminal(client, issue_number) != "open"


def _classify_child_terminal(client: GitHubClient, issue_number: int) -> str:
    """Classify a child Issue for C4: open | merged | closed_without_merge."""
    try:
        issue = client.issue_get(issue_number, fields=["state", "labels"])
    except Exception:
        return "open"
    labels = label_names(issue)
    state = str(issue.get("state", "")).upper()
    if state != "CLOSED":
        return "open"
    if "issuesmith:merge-done" in labels:
        return "merged"
    if labels & TERMINAL_WITHOUT_MERGE:
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


def _parse_table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not _TABLE_ROW_RE.match(stripped):
            continue
        if _TABLE_SEPARATOR_RE.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if cells:
            rows.append(cells)
    return rows


def _extract_change_paths(body: str, *, repo: str | None = None) -> list[str]:
    """Extract change-table paths. When ``repo`` is set, keep only matching リポジトリ rows."""
    paths: list[str] = []
    for match in _CHANGE_TABLE_HEADER.finditer(body):
        start = match.end()
        section = body[start : start + 4000]
        rows = _parse_table_rows(section)
        if len(rows) <= 1:
            continue
        header = [cell.lower() for cell in rows[0]]
        try:
            path_idx = next(
                i for i, cell in enumerate(header) if "ファイルパス" in cell or "パス" in cell
            )
        except StopIteration:
            path_idx = 1 if len(rows[0]) > 1 else 0
        try:
            repo_idx = next(i for i, cell in enumerate(header) if "リポジトリ" in cell)
        except StopIteration:
            repo_idx = None
        for row in rows[1:]:
            if len(row) <= path_idx:
                continue
            if repo is not None and repo_idx is not None:
                if len(row) <= repo_idx:
                    continue
                row_repo = row[repo_idx].strip().strip("`")
                if row_repo != repo:
                    continue
            path = row[path_idx].strip().strip("`")
            if path and "/" in path:
                paths.append(path)
    return paths


def _plan_section(body: str) -> str | None:
    match = re.search(
        r"^###\s+サブイシュー分割計画\s*\n(.*?)(?=^##|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def _expected_child_target_repo(parent_body: str, child: dict[str, Any]) -> str | None:
    """Resolve V1 expected target_repo from the parent split plan, else parent YAML."""
    parent_repo = _target_repo_from_body(parent_body)
    section = _plan_section(parent_body)
    if section is None:
        return parent_repo
    rows = _parse_table_rows(section)
    if len(rows) <= 1:
        return parent_repo
    header = rows[0]
    try:
        title_idx = next(i for i, cell in enumerate(header) if "タイトル" in cell)
    except StopIteration:
        return parent_repo
    try:
        repo_idx = next(i for i, cell in enumerate(header) if "対象リポジトリ" in cell)
    except StopIteration:
        return parent_repo

    child_title = str(child.get("title") or "").strip()
    for row in rows[1:]:
        if len(row) <= title_idx:
            continue
        if row[title_idx].strip() != child_title:
            continue
        if len(row) <= repo_idx:
            return parent_repo
        value = row[repo_idx].strip().strip("`")
        return value or parent_repo
    return parent_repo


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


def _paths_covered(allow_paths: list[str], paths: list[str]) -> list[str]:
    missing: list[str] = []
    for path in paths:
        if not any(fnmatch.fnmatch(path, pattern) for pattern in allow_paths):
            missing.append(path)
    return missing


def _dependency_refs_unresolved(body: str) -> list[str]:
    section_match = re.search(
        r"^##\s+依存（先行）\s*\n(.*?)(?=^##|\Z)",
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
        for match in _PLAN_REF_RE.finditer(line):
            failures.append(f"unresolved plan ref #{match.group(1)} in dependency table")
    return failures


def validate_children(
    parent: dict[str, Any],
    children: list[dict[str, Any]],
    *,
    client: GitHubClient,
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
        expected_repo = _expected_child_target_repo(parent_body, child)
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

        allow_paths = _allow_paths_from_body(body)
        child_paths = _extract_change_paths(body, repo=child_repo)
        missing = _paths_covered(allow_paths, child_paths)
        if missing:
            failures.append(f"V2 allow_paths missing: {', '.join(missing)}")

        if _CJK_PLACEHOLDER_RE.search(body):
            failures.append("V3 CJK placeholder detected")

        unresolved = _dependency_refs_unresolved(body)
        if unresolved:
            failures.extend(unresolved)
        deps = extract_dependencies(body)
        for dep in deps:
            try:
                client.issue_get(dep, fields=["number"])
            except Exception:
                failures.append(f"V4 dependency #{dep} not found")

        if DONE_LABEL["draft"] not in labels:
            failures.append("V5 missing issuesmith:draft-done")
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


def _list_milestone_children(client: GitHubClient, milestone_number: int) -> list[dict[str, Any]]:
    try:
        raw = client.api_request(
            f"issues?state=all&milestone={milestone_number}&per_page=100",
            paginate=True,
        )
    except Exception:
        try:
            raw = client.api_request(f"issues?state=all&milestone={milestone_number}&per_page=100")
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


def _has_request(store: QueueStore, snap: QueueSnapshot, issue: int, phase: str) -> bool:
    seen: set[str] = set(snap.active_order) | set(snap.completed_request_ids)
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


def _candidate_parents(
    store: QueueStore,
    snap: QueueSnapshot,
    client: GitHubClient,
) -> set[int]:
    candidates: set[int] = set()
    for key in snap.milestone_chains:
        try:
            candidates.add(int(key))
        except ValueError:
            continue
    for entry in snap.in_flight:
        issue = entry.get("issue")
        if isinstance(issue, int):
            candidates.add(issue)
    for item in _list_open_milestones(client):
        number = item.get("number")
        if isinstance(number, int):
            candidates.add(number)
    return candidates


def _list_open_milestones(client: GitHubClient) -> list[dict[str, Any]]:
    try:
        raw = client.api_request("issues?state=open&labels=scope:milestone&per_page=100", paginate=True)
    except Exception:
        try:
            raw = client.api_request("issues?state=open&labels=scope:milestone&per_page=100")
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


def _halt_chain(store: QueueStore, parent: int, reason: str) -> None:
    store.update_milestone_chain(
        parent,
        {
            "stage": "halted",
            "halted_reason": reason,
        },
    )


def _ensure_parent_comment(client: GitHubClient, parent: int, body: str, marker: str) -> None:
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
    client: GitHubClient,
    config: IssuesmithConfig | MilestoneChainConfig | None = None,
) -> None:
    if isinstance(config, MilestoneChainConfig):
        chain_cfg = config
    else:
        cfg = config or get_config()
        chain_cfg = cfg.milestone_chain
    if not chain_cfg.enabled:
        return

    snap = store.snapshot()
    # C0 (draft-done in_flight release) moved to queue.dispatch_one generic path (#2980).
    parents = _candidate_parents(store, snap, client)

    for parent_num in sorted(parents):
        chain = store.get_milestone_chain(parent_num)
        if chain.get("stage") == "halted":
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

        # C1: draft-done + CP1 intentional hold → enqueue sub.
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

        milestone_number = _milestone_number(parent)
        if milestone_number is None:
            continue

        children = [
            child
            for child in _list_milestone_children(client, milestone_number)
            if child.get("number") != parent_num
        ]

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
            _ensure_parent_comment(
                client,
                parent_num,
                f"確認待ち: #{first} が merge-done 以外で終了",
                "<!-- issuesmith:milestone-chain:closed-without-merge -->",
            )
            _halt_chain(store, parent_num, f"child closed without merge-done: #{first}")
            continue

        # All children are merge-done.
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


def _child_phase_label(labels: set[str]) -> str:
    for phase in ("merge", "develop", "draft", "sub"):
        for kind, mapping in (
            ("done", DONE_LABEL),
            ("running", RUNNING_LABEL),
            ("ready", READY_LABEL),
        ):
            label = mapping.get(phase)
            if label and label in labels:
                return f"{phase}-{kind}"
    return "-"


def milestone_status(parent: int, *, client: GitHubClient | None = None, store: QueueStore | None = None) -> int:
    client = client or GitHubClient()
    store = store or QueueStore()
    try:
        parent_issue = client.issue_get(
            parent,
            fields=["state", "labels", "body", "milestone", "number", "title"],
        )
    except Exception as exc:
        print(f"error: failed to fetch parent #{parent}: {exc}", file=sys.stderr)
        return 1

    chain = store.get_milestone_chain(parent)
    parent_state = str(parent_issue.get("state") or "-")
    print(f"milestone chain #{parent}: stage={chain.get('stage', '-')}")
    print(f"  state: {parent_state}")
    if chain.get("halted_reason"):
        print(f"  halted_reason: {chain['halted_reason']}")
    if chain.get("notified_all_done"):
        print("  notified_all_done: true")
    if chain.get("closed_parent"):
        print("  closed_parent: true")

    milestone_number = _milestone_number(parent_issue)
    if milestone_number is None:
        print("  children: (no milestone object on parent)")
        return 0

    children = [
        child
        for child in _list_milestone_children(client, milestone_number)
        if child.get("number") != parent
    ]
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


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("usage: issuesmith milestone status <parent> | resume <parent>")
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
    print(f"Unknown milestone command: {cmd}", file=sys.stderr)
    return 2
