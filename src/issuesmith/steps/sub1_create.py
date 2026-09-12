"""SUB1 create Python step — split-plan parse, V1–V5, child Issue create (#3166 / #3060)."""

from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
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
from issuesmith.config import StepConfig, get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.engine import resolve, run_guarded
from issuesmith.gate_rules.b1_milestone_subdesign import parse_table_rows
from issuesmith.gate_rules.cp1 import Cp1Rules
from issuesmith.milestone import (
    _list_chain_children,
    _parse_table_rows,
    _plan_section,
    check_v1_target_repo,
    check_v2_allow_paths,
    check_v3_cjk_placeholders,
    ensure_sub1_binding,
    validate_children,
)
from issuesmith.queue_triage import parse_frontmatter_fields
from issuesmith.steps.base import StepContext, StepResult

_DEFAULT_TEMPLATE = "_sub1-body-order.md"
_DEP_REF_RE = re.compile(r"#(\d+)")
_NIKKI_PREFIX = "${NIKKI_ROOT}"
_CHANGE_SECTION_RE = re.compile(
    r"^##\s+変更対象ファイル\s*\n(.*?)(?=^##[^#]|\Z)",
    re.MULTILINE | re.DOTALL,
)


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


def _github_client() -> ForgePort:
    return get_forge()


def _safe_metadata(body: str) -> dict[str, Any]:
    try:
        return parse_issue_metadata(body)
    except ValueError:
        return parse_frontmatter_fields(body) or {}


def _label_names_from_issue(issue: dict[str, Any]) -> list[str]:
    labels = issue.get("labels") or []
    names: list[str] = []
    for item in labels:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            names.append(item["name"])
        elif isinstance(item, str):
            names.append(item)
    return names


def _milestone_number(issue: dict[str, Any]) -> int | None:
    milestone = issue.get("milestone")
    if isinstance(milestone, dict):
        number = milestone.get("number")
        if isinstance(number, int):
            return number
    return None


def _parse_split_plan(
    body: str, *, parent_target_repo: str
) -> tuple[list[PlanRow], bool]:
    """Parse ### サブイシュー分割計画 by header names (column order not fixed)."""
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
    title_i = _idx("タイトル")
    repo_i = _idx("対象リポジトリ")
    scope_i = _idx("内容")
    dep_i = _idx("依存")
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


def _check_cp1_gate(comments: list[dict[str, Any]]) -> str:
    """Return PASS | CP1_NOT_READY | BLOCK (mirrors sub-ready.md §1.5)."""

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


def _parent_design_gate(body: str) -> str | None:
    """Return error comment body fragment, or None if OK."""
    if count_heading(body, "設計") > 1:
        return "## SUB1 エラー: `## 設計` セクションが重複しています"
    design = get_section(body, "設計")
    ac = get_section(body, "受け入れ条件") or get_section_by_keyword(body, "受け入れ条件")
    if not design or not design.strip() or not ac or not ac.strip():
        return (
            "## SUB1 エラー: 設計または受け入れ条件が未記載\n\n"
            "親イシューに `## 設計` と `## 受け入れ条件` の両方が必要です。"
        )
    if not get_subsections(body, "設計", "#### サブ"):
        return (
            "## SUB1 エラー: サブイシュー詳細設計が未生成（B1 未完了の可能性）\n\n"
            "親イシューの `## 設計` に `#### サブN` サブセクションがありません。"
        )
    return None


def _resolve_dependencies(
    dep_raw: str,
    *,
    table_row_count: int,
    row_to_issue: dict[int, int],
    client: ForgePort,
    state: Sub1State,
) -> str:
    dep = (dep_raw or "").strip()
    if not dep or dep == "なし":
        return "なし"
    resolved = dep
    seen: list[int] = []
    for match in _DEP_REF_RE.finditer(dep):
        k = int(match.group(1))
        if k in seen:
            continue
        seen.append(k)
        if k <= table_row_count:
            if k in row_to_issue:
                target = f"#{row_to_issue[k]}"
                resolved = resolved.replace(f"#{k}", target, 1)
                state.resolved_logs.append(f"連番解決: テーブル{k} → {target}")
            else:
                state.unresolved_forward_logs.append(f"未解決の前方参照: テーブル{k}")
            continue
        try:
            issue = client.issue_get(k, fields=["labels"])
            labels = _label_names_from_issue(issue)
        except Exception:
            labels = []
        if "scope:milestone" in labels:
            resolved = resolved.replace(f"#{k}", "", 1)
            state.excluded_milestone_logs.append(f"除外した scope:milestone 依存: #{k}")
    resolved = re.sub(r"\s+", " ", resolved).strip(" ,;|")
    if not resolved or resolved == "なし":
        return "なし"
    return resolved


def _allow_paths_for_row(parent_body: str, row: PlanRow) -> list[str]:
    """Extract allow_paths for ROW_REPO from #### サブN change table (or parent)."""
    paths: list[str] = []
    sub_secs = get_subsections(parent_body, "設計", "#### サブ")
    prefix = f"#### サブ{row.row_num}:"
    sub_body = ""
    for heading, content in sub_secs:
        if heading.startswith(prefix):
            sub_body = content
            break
    section = get_section(sub_body, "変更対象ファイル") if sub_body else None
    if section:
        rows = parse_table_rows(section)
        if len(rows) > 1:
            header = [c.lower() for c in rows[0]]
            try:
                repo_i = next(i for i, c in enumerate(header) if "リポジトリ" in c)
            except StopIteration:
                repo_i = 0
            try:
                path_i = next(
                    i for i, c in enumerate(header) if "ファイルパス" in c or "パス" in c
                )
            except StopIteration:
                path_i = 1
            for cells in rows[1:]:
                if len(cells) <= max(repo_i, path_i):
                    continue
                repo = cells[repo_i].strip().strip("`")
                if repo != row.repo:
                    continue
                path = cells[path_i].strip().strip("`")
                path = re.sub(r"\([^)]*\)", "", path).strip()
                if path.startswith("/var/tmp/"):
                    print(f"WARN: skip invalid allow_path {path!r}", file=sys.stderr)
                    continue
                if path.startswith(_NIKKI_PREFIX):
                    continue
                if path:
                    paths.append(path)
    if not paths:
        parent_meta = _safe_metadata(parent_body)
        allow = parent_meta.get("allow_paths")
        if isinstance(allow, list):
            paths = [str(p) for p in allow if isinstance(p, str)]
    return paths


def _build_dep_section(
    resolved_dep: str, *, row_repo: str, client: ForgePort
) -> str:
    if not resolved_dep or resolved_dep == "なし":
        return ""
    nums = [int(m.group(1)) for m in _DEP_REF_RE.finditer(resolved_dep)]
    if not nums:
        return ""
    lines = [
        "## 依存（先行）",
        "",
        "| # | 依存先 | 状態 |",
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
        dep_repo = str(_safe_metadata(str(data.get("body") or "")).get("target_repo") or "")
        if row_repo == "sumipan/nexus" and dep_repo and dep_repo != "sumipan/nexus":
            cross_repo = True
        lines.append(f"| {idx} | #{num} ({title}) | {state} |")
    if cross_repo:
        lines.append("")
        lines.append(
            "> 外部リポジトリのリリースと release-watcher の bump Issue が "
            "merge-done になってからこの子を投入する"
        )
    return "\n".join(lines) + "\n"


def _sub_section_parts(parent_body: str, row_num: int) -> dict[str, str]:
    sub_secs = get_subsections(parent_body, "設計", "#### サブ")
    prefix = f"#### サブ{row_num}:"
    for heading, content in sub_secs:
        if heading.startswith(prefix):
            return {
                "scope": (get_section(content, "スコープ") or "").strip(),
                "design": (get_section(content, "設計方針") or "").strip(),
                "files": (get_section(content, "変更対象ファイル") or "").strip(),
                "ac": (get_section(content, "受け入れ条件") or "").strip(),
            }
    return {}


def _build_child_body(
    *,
    parent_body: str,
    parent_number: int,
    row: PlanRow,
    resolved_dep: str,
    client: ForgePort,
    parent_labels: list[str],
) -> str:
    parent_meta = _safe_metadata(parent_body)
    allow_paths = _allow_paths_for_row(parent_body, row)
    yaml_lines = [f"target_repo: {row.repo}"]
    base = parent_meta.get("base_branch")
    if isinstance(base, str) and base.strip():
        yaml_lines.append(f"base_branch: {base.strip()}")
    if allow_paths:
        yaml_lines.append("allow_paths:")
        for path in allow_paths:
            yaml_lines.append(f'  - "{path}"')
    diary_allow = parent_meta.get("diary_allow_paths")
    has_diary = any(p.startswith(_NIKKI_PREFIX) for p in allow_paths)
    # diary paths were filtered out of allow_paths; re-scan change table for NIKKI
    if isinstance(diary_allow, list) and diary_allow:
        # inherit when parent has diary_allow_paths (conservative)
        yaml_lines.append("diary_allow_paths:")
        for path in diary_allow:
            if isinstance(path, str):
                yaml_lines.append(f'  - "{path}"')
        _ = has_diary  # reserved for stricter filter

    parts = [
        "```yaml",
        *yaml_lines,
        "```",
        "",
        f"親イシュー: #{parent_number}",
        f"依存: {resolved_dep}",
        "",
    ]
    dep_section = _build_dep_section(resolved_dep, row_repo=row.repo, client=client)
    if dep_section:
        parts.append(dep_section)

    sub = _sub_section_parts(parent_body, row.row_num)
    if sub:
        parts.append("## スコープ")
        parts.append(sub.get("scope") or row.scope or "")
        parts.append("")
        parts.append("## 設計")
        parts.append(f"> 親イシュー #{parent_number} サブ{row.row_num} から導出")
        parts.append("")
        if sub.get("design"):
            parts.append(sub["design"])
            parts.append("")
        if sub.get("files"):
            parts.append("## 変更対象ファイル")
            parts.append(sub["files"])
            parts.append("")
        parts.append("## 受け入れ条件")
        parts.append(sub.get("ac") or "- [ ] (from parent)")
        parts.append("")
    else:
        parts.append("## スコープ")
        parts.append(row.scope or "")
        parts.append("")
        design = get_section(parent_body, "設計") or ""
        parts.append("## 設計")
        parts.append(f"> 親イシュー #{parent_number} サブ{row.row_num} から導出")
        parts.append("")
        parts.append(design.strip())
        parts.append("")
        ac = get_section(parent_body, "受け入れ条件") or get_section_by_keyword(
            parent_body, "受け入れ条件"
        )
        parts.append("## 受け入れ条件")
        parts.append((ac or "").strip())
        parts.append("")

    if "scope:migration" in parent_labels:
        filtered = _filter_parent_section(parent_body, row.row_num, "影響範囲調査（scope:migration 時は必須）")
        if filtered:
            parts.append("## 影響範囲調査（scope:migration 時は必須）")
            parts.append(filtered)
            parts.append("")
    breaking = _filter_parent_section(parent_body, row.row_num, "破壊的変更の影響範囲")
    if breaking:
        parts.append("## 破壊的変更の影響範囲")
        parts.append(breaking)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _filter_parent_section(parent_body: str, row_num: int, heading: str) -> str:
    sub_secs = get_subsections(parent_body, "設計", "#### サブ")
    sub_body = ""
    prefix = f"#### サブ{row_num}:"
    for h, content in sub_secs:
        if h.startswith(prefix):
            sub_body = content
            break
    sub_paths: list[str] = []
    tbl = get_section(sub_body, "変更対象ファイル") if sub_body else None
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
            if cell and cell not in ("ファイルパス",):
                sub_paths.append(cell)
    section_content = get_section(parent_body, heading)
    if not section_content or not sub_paths:
        return ""
    result = filter_section_by_paths(section_content, sub_paths)
    return "\n".join(result) if result else ""


def _change_paths_for_repo(body: str, target_repo: str) -> list[str]:
    paths: list[str] = []
    match = _CHANGE_SECTION_RE.search(body)
    if not match:
        # bold-header style used by validate_children helpers
        from issuesmith.milestone import _extract_change_paths

        return _extract_change_paths(body, repo=target_repo)
    rows = parse_table_rows(match.group(1))
    for row in rows[1:]:
        if len(row) < 2:
            continue
        repo = row[0].strip().strip("`").replace("`", "")
        path = row[1].strip().strip("`").replace("`", "")
        if repo != target_repo:
            continue
        if path:
            paths.append(path)
    return paths


def _prevalidate_child_body(
    *,
    body: str,
    row_repo: str,
    parent_issue_number: int,
    resolved_dep: str,
    client: ForgePort,
    supported: frozenset[str] | set[str],
) -> list[str]:
    """Pre-creation V1–V5. V1–V3 via shared milestone helpers."""
    meta = _safe_metadata(body)
    child_repo = str(meta.get("target_repo") or "").strip() or None
    failures = check_v1_target_repo(child_repo, row_repo, supported)

    allow_paths_raw = meta.get("allow_paths") or []
    allow_paths = [str(p) for p in allow_paths_raw if isinstance(p, str)]
    paths = _change_paths_for_repo(body, child_repo or row_repo)
    # Prefer pre-creation style messages when helper reports missing paths
    v2 = check_v2_allow_paths(allow_paths, paths)
    for item in v2:
        if item.startswith("V2 allow_paths missing:"):
            missing = item.split(":", 1)[1].strip()
            for path in [p.strip() for p in missing.split(",") if p.strip()]:
                failures.append(f"V2: allow_paths 未包含 ({path})")
        else:
            failures.append(item)

    failures.extend(check_v3_cjk_placeholders(allow_paths=allow_paths))

    dep = (resolved_dep or "").strip()
    if dep and dep != "なし":
        if "## 依存（先行）" not in body:
            failures.append("V4: 依存ありだが ## 依存（先行） セクション欠落")
        for num_str in _DEP_REF_RE.findall(dep):
            num = int(num_str)
            if num == parent_issue_number:
                failures.append(f"V5: 依存が親 Issue 自身を参照 (#{num})")
                continue
            try:
                labels = _label_names_from_issue(
                    client.issue_get(num, fields=["labels"])
                )
            except Exception:
                labels = []
            if "scope:milestone" in labels:
                failures.append(f"V5: 依存が scope:milestone Issue を参照 (#{num})")
    return failures


def _create_child_issue(
    client: ForgePort,
    *,
    title: str,
    body: str,
    labels: list[str] | None,
    milestone: int | None,
) -> int:
    return int(
        client.issue_create(title, body, labels=labels, milestone=milestone)
    )


def _resolve_template(step: StepConfig | None) -> str | None:
    if step is not None and step.template:
        return step.template
    cfg_step = get_config().steps.get("sub1")
    if cfg_step is not None and cfg_step.template:
        return cfg_step.template
    template_dir = get_config().paths.template_dir
    candidate = template_dir / _DEFAULT_TEMPLATE
    if candidate.is_file():
        return _DEFAULT_TEMPLATE
    return None


def _run_guarded_body(
    ctx: StepContext,
    *,
    row: PlanRow,
    body_path: Path,
    template_name: str,
) -> int:
    """LLM body generation via run-guarded implementation (optional template)."""
    template = str(get_config().paths.template_dir / template_name)
    selection = resolve("implementation", "default")
    variables = [
        f"issue_number={ctx.issue_number}",
        f"row_num={row.row_num}",
        f"row_title={row.title}",
        f"row_repo={row.repo}",
        f"sub_body_path={body_path}",
        f"target_repo={ctx.target_repo}",
        f"model={selection.model}",
    ]
    return run_guarded(
        "implementation",
        template,
        variables,
        success_statuses=["SUB_BODY_READY", "SUB_CREATED"],
        failure_status="SUB_BODY_FAILED",
    )


def _fail(
    client: ForgePort, issue_number: int, comment: str, status: str = "IMPL_FAILED"
) -> StepResult:
    if "PIPELINE_STATUS:" not in comment:
        comment = comment.rstrip() + f"\n\nPIPELINE_STATUS: {status}\n"
    try:
        client.issue_comment(issue_number, comment)
    except Exception as exc:
        print(f"WARN: comment failed: {exc}", file=sys.stderr)
    return StepResult(exit_code=1, pipeline_status=status)


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute SUB1: parse plan, pre-validate, create children, post-validate."""
    issue_number = int(ctx.issue_number)
    client = _github_client()
    parent = client.issue_get(
        issue_number,
        fields=["number", "body", "labels", "milestone", "comments", "title"],
    )
    parent_body = str(parent.get("body") or "")
    labels = _label_names_from_issue(parent)
    comments = parent.get("comments") or []
    if not isinstance(comments, list):
        comments = []

    if "scope:milestone" not in labels:
        return _fail(
            client,
            issue_number,
            "## SUB1 エラー: scope:milestone なし\n\n"
            "`issuesmith:sub-ready` は `scope:milestone` ラベル付き Issue にのみ使用できます。",
        )

    cp1 = _check_cp1_gate([c for c in comments if isinstance(c, dict)])
    if cp1 == "CP1_NOT_READY":
        return _fail(
            client,
            issue_number,
            "## SUB1 エラー: CP1 未完了\n\n"
            "B1 ブラッシュアップ後の CP1 ゲートがまだ完了していません。",
        )
    if cp1 == "BLOCK":
        return _fail(
            client,
            issue_number,
            "## SUB1 エラー: CP1 未通過\n\n"
            "親 Issue の CP1 ゲートが FAIL（intentional_hold 以外）の状態です。",
        )

    milestone_number = _milestone_number(parent)
    if milestone_number is None:
        try:
            client.issue_comment(
                issue_number,
                "## SUB1 警告: milestone 未設定\n\n"
                "親 Issue に milestone が設定されていません。サブイシューリンクで続行します。\n",
            )
        except Exception:
            pass

    design_err = _parent_design_gate(parent_body)
    if design_err:
        return _fail(client, issue_number, design_err)

    parent_meta = _safe_metadata(parent_body)
    parent_target_repo = str(parent_meta.get("target_repo") or "").strip()
    plan_rows, has_repo_col = _parse_split_plan(
        parent_body, parent_target_repo=parent_target_repo
    )
    if not plan_rows:
        return _fail(
            client,
            issue_number,
            "## SUB1 エラー: サブイシュー分割計画が見つかりません",
        )
    if not has_repo_col and not parent_target_repo:
        return _fail(
            client,
            issue_number,
            "## SUB1 エラー: 親 YAML に target_repo がありません\n\n"
            f"親 Issue #{issue_number} の YAML ブロックに `target_repo` フィールドが必要です。",
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
    template_name = _resolve_template(step)

    for row in plan_rows:
        if has_repo_col and not row.repo:
            state.validation_failures.append(
                f"サブ{row.row_num}: 5 列表で対象リポジトリが空"
            )
            continue

        if row.title in existing:
            child_num = existing[row.title]
            state.row_to_issue[row.row_num] = child_num
            if not ensure_sub1_binding(client, issue_number, child_num):
                return _fail(
                    client,
                    issue_number,
                    f"## SUB1 エラー: 既存子 #{child_num} のサブイシューリンクに失敗",
                )
            state.created_issues.append(f"既存: #{child_num} / {row.title}（スキップ）")
            continue

        resolved_dep = _resolve_dependencies(
            row.dep_raw,
            table_row_count=table_row_count,
            row_to_issue=state.row_to_issue,
            client=client,
            state=state,
        )
        body = _build_child_body(
            parent_body=parent_body,
            parent_number=issue_number,
            row=row,
            resolved_dep=resolved_dep,
            client=client,
            parent_labels=labels,
        )

        if template_name:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(body)
                tmp_path = Path(tmp.name)
            try:
                rc = _run_guarded_body(
                    ctx, row=row, body_path=tmp_path, template_name=template_name
                )
                if rc == 0 and tmp_path.is_file():
                    generated = tmp_path.read_text(encoding="utf-8")
                    if generated.strip():
                        body = generated
            except Exception as exc:
                print(f"WARN: run-guarded body skipped: {exc}", file=sys.stderr)
            finally:
                tmp_path.unlink(missing_ok=True)

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
                    f"## SUB1 エラー: サブ{row.row_num} body に CP1 禁則語が残存\n\n"
                    f"{detail}\n\nPIPELINE_STATUS: SUB1_CP1_BLOCKED\n",
                )
            except Exception:
                pass
            state.validation_failures.append(f"サブ{row.row_num}: CP1 禁則語")
            continue

        pre_fail = _prevalidate_child_body(
            body=body,
            row_repo=row.repo,
            parent_issue_number=issue_number,
            resolved_dep=resolved_dep,
            client=client,
            supported=supported,
        )
        if pre_fail:
            state.validation_failures.append(
                f"サブ{row.row_num}:\n" + "\n".join(pre_fail)
            )
            continue

        scope_labels = [
            lab for lab in labels if lab.startswith("scope:") and lab != "scope:milestone"
        ]
        create_labels = ["issuesmith:draft-done", *scope_labels]
        try:
            new_number = _create_child_issue(
                client,
                title=row.title,
                body=body,
                labels=create_labels,
                milestone=milestone_number,
            )
        except Exception as exc:
            return _fail(
                client,
                issue_number,
                f"## SUB1 エラー: Issue 作成失敗（サブ{row.row_num}）\n\n{exc}",
            )

        # Ensure draft-done even if create ignored labels
        try:
            client.issue_update(
                new_number, labels_add=["issuesmith:draft-done", *scope_labels]
            )
        except Exception as exc:
            print(f"WARN: label update failed for #{new_number}: {exc}", file=sys.stderr)

        if not ensure_sub1_binding(client, issue_number, new_number):
            return _fail(
                client,
                issue_number,
                f"## SUB1 エラー: 子 #{new_number} のサブイシューリンクに失敗",
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

    if state.validation_failures and not state.created_children and not any(
        s.startswith("既存:") for s in state.created_issues
    ):
        fail_body = "\n".join(f"- {entry}" for entry in state.validation_failures)
        return _fail(
            client,
            issue_number,
            "## SUB1 エラー: 子 Issue body 検証失敗\n\n"
            "以下の行で pre-creation validation (V1–V5) が失敗したため "
            "Issue を作成しませんでした:\n\n"
            f"{fail_body}\n",
        )

    if state.validation_failures:
        fail_body = "\n".join(f"- {entry}" for entry in state.validation_failures)
        try:
            client.issue_comment(
                issue_number,
                "## SUB1 警告: 一部行の検証失敗\n\n" + fail_body + "\n",
            )
        except Exception:
            pass

    if state.created_children:
        post = validate_children(parent, state.created_children, client=client)
        if not post.passed:
            details = []
            for item in post.results:
                if not item.passed:
                    details.append(
                        f"#{item.issue}: " + "; ".join(item.failures)
                    )
            return _fail(
                client,
                issue_number,
                "## SUB1 エラー: 作成後 validate_children 失敗\n\n"
                + "\n".join(details),
            )

    created_block = "\n".join(f"- {line}" for line in state.created_issues) or "- (なし)"
    extra_logs = []
    extra_logs.extend(state.resolved_logs)
    extra_logs.extend(state.excluded_milestone_logs)
    extra_logs.extend(state.unresolved_forward_logs)
    log_block = ("\n" + "\n".join(extra_logs) + "\n") if extra_logs else "\n"
    ms = str(milestone_number) if milestone_number is not None else "なし（サブイシューリンクのみ）"
    try:
        client.issue_comment(
            issue_number,
            "## SUB1 サブイシュー作成完了\n\n"
            f"作成したサブイシュー:\n{created_block}\n\n"
            f"milestone: {ms}\n"
            f"{log_block}\n"
            "検証済みの子 develop request は queue が依存順に自動投入する。\n",
        )
    except Exception as exc:
        print(f"WARN: completion comment failed: {exc}", file=sys.stderr)

    if not state.created_issues:
        return StepResult(exit_code=1, pipeline_status="IMPL_FAILED")
    return StepResult(exit_code=0, pipeline_status="SUB_CREATED")
