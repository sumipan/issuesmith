"""Aggregate rework metrics from paths.metrics and optional ghdag audit (#4431)."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ghdag.metrics.models import FailureClass

from issuesmith.config import IssuesmithConfig

_TRANSIENT_FAILURES = frozenset(
    {
        FailureClass.TIMEOUT.value,
        FailureClass.ENGINE_ERROR.value,
        FailureClass.QUOTA_EXHAUSTED.value,
    }
)

_EVENT_NAMES = frozenset(
    {
        "step_started",
        "requires_check",
        "requires_repair",
        "andon_raised",
        "andon_answered",
    }
)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def parse_since(value: str, tz: ZoneInfo) -> datetime:
    parts = value.split("-")
    if len(parts) != 3:
        raise ValueError(f"invalid --since: {value}")
    try:
        year, month, day = (int(parts[0]), int(parts[1]), int(parts[2]))
        return datetime(year, month, day, tzinfo=tz)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"invalid --since: {value}") from exc


def _parse_ts(value: Any, tz: ZoneInfo) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=tz)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def _row_timestamp(row: Mapping[str, Any], tz: ZoneInfo) -> datetime | None:
    event = row.get("event")
    if event in _EVENT_NAMES:
        return _parse_ts(row.get("ts"), tz)
    return _parse_ts(row.get("timestamp"), tz)


def _issue_from_row(row: Mapping[str, Any]) -> int | None:
    raw = row.get("issue")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _week_info(dt: datetime) -> tuple[str, str]:
    local = dt.date()
    iso = local.isocalendar()
    week_label = f"{iso.year}-W{iso.week:02d}"
    monday = local - timedelta(days=iso.weekday - 1)
    return week_label, monday.isoformat()


def _basename_template(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).name


def resolve_cause_target(cause: str, cause_targets: Mapping[str, str]) -> str | None:
    matches = [(prefix, target) for prefix, target in cause_targets.items() if cause.startswith(prefix)]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


def _audit_failure_class(audit_rows: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in audit_rows:
        event = str(row.get("event") or row.get("event_type") or "")
        if event not in {"task_failed", "task_rejected"}:
            continue
        uuid = str(row.get("uuid") or "").strip()
        failure = str(row.get("failure_class") or "").strip()
        if uuid and failure:
            out[uuid] = failure
    return out


@dataclass
class Execution:
    issue: int
    step: str
    parent_uuid: str
    ts: datetime
    workflow: str = ""


@dataclass
class ReworkItem:
    kind: str
    issue: int
    step: str
    origin_step: str
    cause: str
    ts: str
    week: str


@dataclass
class _IssueState:
    executions: list[Execution] = field(default_factory=list)
    repairs: list[ReworkItem] = field(default_factory=list)
    humans: list[ReworkItem] = field(default_factory=list)
    reruns: list[ReworkItem] = field(default_factory=list)
    noise: list[ReworkItem] = field(default_factory=list)
    runs_by_step: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    unpriced_calls: int = 0
    unpriced_tokens: int = 0


def _execution_success(
    parent_uuid: str,
    shell_by_uuid: Mapping[str, dict[str, Any]],
) -> bool:
    row = shell_by_uuid.get(parent_uuid)
    if row is None:
        return False
    return str(row.get("status", "")) == "success"


def _execution_failure_class(
    parent_uuid: str,
    *,
    audit_by_uuid: Mapping[str, str],
    llm_failures_by_parent: Mapping[str, list[tuple[datetime, str]]],
    shell_by_uuid: Mapping[str, dict[str, Any]],
) -> str | None:
    if parent_uuid in audit_by_uuid:
        return audit_by_uuid[parent_uuid]
    failures = llm_failures_by_parent.get(parent_uuid) or []
    if failures:
        return failures[-1][1]
    row = shell_by_uuid.get(parent_uuid)
    if row is not None:
        failure = str(row.get("failure_class") or "").strip()
        if failure:
            return failure
    return None


def _shell_by_uuid(task_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in task_rows:
        uuid = str(row.get("uuid") or "").strip()
        if uuid:
            out[uuid] = row
    return out


def _llm_failures_by_parent(
    task_rows: list[dict[str, Any]], tz: ZoneInfo
) -> dict[str, list[tuple[datetime, str]]]:
    out: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for row in task_rows:
        parent = str(row.get("parent_uuid") or "").strip()
        if not parent:
            continue
        status = str(row.get("status") or "")
        failure = str(row.get("failure_class") or "").strip()
        if status in {"success", "completed"} or not failure:
            continue
        ts = _row_timestamp(row, tz)
        if ts is not None:
            out[parent].append((ts, failure))
    for parent in out:
        out[parent].sort(key=lambda item: item[0])
    return out


def _events_for_parent(
    parent_uuid: str,
    repairs: list[dict[str, Any]],
    andons: list[dict[str, Any]],
    tz: ZoneInfo,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rep = [r for r in repairs if str(r.get("parent_uuid") or "") == parent_uuid]
    andon = [a for a in andons if str(a.get("parent_uuid") or "") == parent_uuid]
    rep.sort(key=lambda r: _row_timestamp(r, tz) or datetime.min.replace(tzinfo=tz))
    andon.sort(key=lambda r: _row_timestamp(r, tz) or datetime.min.replace(tzinfo=tz))
    return rep, andon


def _cause_from_execution(
    exec_row: Execution,
    *,
    repairs: list[dict[str, Any]],
    andons: list[dict[str, Any]],
    audit_by_uuid: Mapping[str, str],
    llm_failures_by_parent: Mapping[str, list[tuple[datetime, str]]],
    shell_by_uuid: Mapping[str, dict[str, Any]],
    tz: ZoneInfo,
) -> tuple[str, str]:
    rep, andon = _events_for_parent(exec_row.parent_uuid, repairs, andons, tz)
    if rep:
        rule_ids = rep[-1].get("rule_ids") or []
        if rule_ids:
            return str(rep[-1].get("step") or exec_row.step), str(rule_ids[0])
    raised = [a for a in andon if a.get("event") == "andon_raised"]
    if raised:
        kind = str(raised[-1].get("kind") or "unknown")
        return str(raised[-1].get("step") or exec_row.step), f"andon:{kind}"
    failure = _execution_failure_class(
        exec_row.parent_uuid,
        audit_by_uuid=audit_by_uuid,
        llm_failures_by_parent=llm_failures_by_parent,
        shell_by_uuid=shell_by_uuid,
    )
    if failure:
        return exec_row.step, f"failure:{failure}"
    return exec_row.step, "unknown"


def _last_failed_execution_between(
    issue: int,
    start: datetime,
    end: datetime,
    executions: list[Execution],
    *,
    shell_by_uuid: Mapping[str, dict[str, Any]],
    audit_by_uuid: Mapping[str, str],
    llm_failures_by_parent: Mapping[str, list[tuple[datetime, str]]],
) -> Execution | None:
    candidates = [
        ex
        for ex in executions
        if ex.issue == issue and start < ex.ts < end and not _execution_success(ex.parent_uuid, shell_by_uuid)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda ex: ex.ts)
    for ex in reversed(candidates):
        failure = _execution_failure_class(
            ex.parent_uuid,
            audit_by_uuid=audit_by_uuid,
            llm_failures_by_parent=llm_failures_by_parent,
            shell_by_uuid=shell_by_uuid,
        )
        if failure or not _execution_success(ex.parent_uuid, shell_by_uuid):
            return ex
    return candidates[-1]


def compute(
    metrics_path: Path,
    *,
    audit_path: Path | None = None,
    config: IssuesmithConfig,
    since: date | datetime | None = None,
    issue_filter: int | None = None,
) -> dict[str, Any]:
    tz = ZoneInfo(config.timezone)
    since_dt: datetime | None = None
    if since is not None:
        if isinstance(since, datetime):
            since_dt = since.astimezone(tz) if since.tzinfo else since.replace(tzinfo=tz)
        else:
            since_dt = datetime(since.year, since.month, since.day, tzinfo=tz)

    skipped_rows = 0
    event_rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    for row in load_jsonl(metrics_path):
        ts = _row_timestamp(row, tz)
        if ts is None:
            skipped_rows += 1
            continue
        # LLM rows carry no ``issue``; keep them and attribute via parent_uuid below.
        row_issue = _issue_from_row(row)
        if issue_filter is not None and row_issue is not None and row_issue != issue_filter:
            continue
        if since_dt is not None and ts < since_dt:
            continue
        row["_parsed_ts"] = ts
        if row.get("event") in _EVENT_NAMES:
            event_rows.append(row)
        elif row.get("uuid") is not None:
            task_rows.append(row)

    audit_rows = load_jsonl(audit_path) if audit_path is not None else []
    audit_by_uuid = _audit_failure_class(audit_rows)
    shell_by_uuid = _shell_by_uuid(task_rows)
    llm_failures_by_parent = _llm_failures_by_parent(task_rows, tz)

    step_started: dict[str, Execution] = {}
    for row in event_rows:
        if row.get("event") != "step_started":
            continue
        parent = str(row.get("parent_uuid") or "").strip()
        issue = _issue_from_row(row)
        step = str(row.get("step") or "").strip()
        if not parent or issue is None or not step:
            continue
        ts = row["_parsed_ts"]
        existing = step_started.get(parent)
        if existing is None or ts < existing.ts:
            step_started[parent] = Execution(
                issue=issue,
                step=step,
                parent_uuid=parent,
                ts=ts,
                workflow=str(row.get("workflow") or ""),
            )

    all_executions = sorted(step_started.values(), key=lambda ex: (ex.issue, ex.step, ex.ts))
    repairs_raw = [r for r in event_rows if r.get("event") == "requires_repair"]
    andons_raw = [r for r in event_rows if r.get("event") in {"andon_raised", "andon_answered"}]
    andon_raised_rows = [r for r in event_rows if r.get("event") == "andon_raised"]

    issues: dict[int, _IssueState] = defaultdict(_IssueState)
    rework_items: list[ReworkItem] = []
    noise_items: list[ReworkItem] = []

    exec_by_issue_step: dict[tuple[int, str], list[Execution]] = defaultdict(list)
    for ex in all_executions:
        exec_by_issue_step[(ex.issue, ex.step)].append(ex)
        issues[ex.issue].runs_by_step[ex.step] = issues[ex.issue].runs_by_step.get(ex.step, 0) + 1

    for (issue, step), execs in exec_by_issue_step.items():
        for idx, current in enumerate(execs):
            if idx == 0:
                continue
            previous = execs[idx - 1]
            failure = _execution_failure_class(
                previous.parent_uuid,
                audit_by_uuid=audit_by_uuid,
                llm_failures_by_parent=llm_failures_by_parent,
                shell_by_uuid=shell_by_uuid,
            )
            week_label, _ = _week_info(current.ts)
            if failure in _TRANSIENT_FAILURES:
                item = ReworkItem(
                    kind="noise",
                    issue=issue,
                    step=step,
                    origin_step=previous.step,
                    cause=f"failure:{failure}" if failure else "unknown",
                    ts=current.ts.isoformat(),
                    week=week_label,
                )
                noise_items.append(item)
                issues[issue].noise.append(item)
                continue
            origin_step, cause = _cause_from_execution(
                previous,
                repairs=repairs_raw,
                andons=andons_raw,
                audit_by_uuid=audit_by_uuid,
                llm_failures_by_parent=llm_failures_by_parent,
                shell_by_uuid=shell_by_uuid,
                tz=tz,
            )
            if _execution_success(previous.parent_uuid, shell_by_uuid):
                failed = _last_failed_execution_between(
                    issue,
                    previous.ts,
                    current.ts,
                    all_executions,
                    shell_by_uuid=shell_by_uuid,
                    audit_by_uuid=audit_by_uuid,
                    llm_failures_by_parent=llm_failures_by_parent,
                )
                if failed is not None:
                    origin_step, cause = _cause_from_execution(
                        failed,
                        repairs=repairs_raw,
                        andons=andons_raw,
                        audit_by_uuid=audit_by_uuid,
                        llm_failures_by_parent=llm_failures_by_parent,
                        shell_by_uuid=shell_by_uuid,
                        tz=tz,
                    )
            item = ReworkItem(
                kind="rerun",
                issue=issue,
                step=step,
                origin_step=origin_step,
                cause=cause,
                ts=current.ts.isoformat(),
                week=week_label,
            )
            rework_items.append(item)
            issues[issue].reruns.append(item)

    for row in repairs_raw:
        issue = _issue_from_row(row)
        if issue is None:
            continue
        step = str(row.get("step") or "").strip()
        rule_ids = row.get("rule_ids") or []
        cause = str(rule_ids[0]) if rule_ids else "unknown"
        ts = row["_parsed_ts"]
        week_label, _ = _week_info(ts)
        item = ReworkItem(
            kind="repair",
            issue=issue,
            step=step,
            origin_step=step,
            cause=cause,
            ts=ts.isoformat(),
            week=week_label,
        )
        rework_items.append(item)
        issues[issue].repairs.append(item)

    repair_templates = set(config.metrics.repair_templates)
    if repair_templates:
        for row in task_rows:
            template = _basename_template(row.get("template"))
            if template not in repair_templates:
                continue
            parent = str(row.get("parent_uuid") or "").strip()
            issue = _issue_from_row(row)
            if not parent or issue is None:
                continue
            exec_row = step_started.get(parent)
            step = exec_row.step if exec_row else str(row.get("step") or "")
            rep, _ = _events_for_parent(parent, repairs_raw, andons_raw, tz)
            if rep and rep[-1].get("rule_ids"):
                cause = str(rep[-1]["rule_ids"][0])
                origin_step = str(rep[-1].get("step") or step)
            else:
                cause = f"template:{template}"
                origin_step = step
            ts = row["_parsed_ts"]
            week_label, _ = _week_info(ts)
            item = ReworkItem(
                kind="repair",
                issue=issue,
                step=step,
                origin_step=origin_step,
                cause=cause,
                ts=ts.isoformat(),
                week=week_label,
            )
            rework_items.append(item)
            issues[issue].repairs.append(item)

    for row in event_rows:
        if row.get("event") != "andon_answered":
            continue
        issue = _issue_from_row(row)
        if issue is None:
            continue
        step = str(row.get("step") or "").strip()
        kind = str(row.get("kind") or "unknown")
        ts = row["_parsed_ts"]
        week_label, _ = _week_info(ts)
        item = ReworkItem(
            kind="human",
            issue=issue,
            step=step,
            origin_step=step,
            cause=f"andon:{kind}",
            ts=ts.isoformat(),
            week=week_label,
        )
        rework_items.append(item)
        issues[issue].humans.append(item)

    week_cost: dict[str, float] = defaultdict(float)
    week_unpriced_calls: dict[str, int] = defaultdict(int)
    week_unpriced_tokens: dict[str, int] = defaultdict(int)
    parent_uuids = set(step_started)
    for row in task_rows:
        parent = str(row.get("parent_uuid") or "").strip()
        if parent not in parent_uuids:
            continue
        issue = _issue_from_row(row)
        if issue is None:
            exec_row = step_started.get(parent)
            if exec_row is None:
                continue
            issue = exec_row.issue
        engine = str(row.get("engine") or "")
        if engine == "shell":
            continue
        cost_week, _ = _week_info(row["_parsed_ts"])
        cost = row.get("cost_usd")
        if cost is None:
            token_count = row.get("token_count")
            if token_count is None:
                continue
            issues[issue].unpriced_calls += 1
            week_unpriced_calls[cost_week] += 1
            if isinstance(token_count, int):
                issues[issue].unpriced_tokens += token_count
                week_unpriced_tokens[cost_week] += token_count
        else:
            try:
                value = float(cost)
            except (TypeError, ValueError):
                continue
            issues[issue].cost_usd += value
            week_cost[cost_week] += value

    done_step = config.metrics.done_step
    done_by_week: dict[str, set[int]] = defaultdict(set)
    first_pass_by_week: dict[str, set[int]] = defaultdict(set)
    for ex in all_executions:
        if ex.step != done_step:
            continue
        if not _execution_success(ex.parent_uuid, shell_by_uuid):
            continue
        shell_row = shell_by_uuid.get(ex.parent_uuid)
        done_ts = ex.ts
        if shell_row is not None:
            shell_ts = _row_timestamp(shell_row, tz)
            if shell_ts is not None:
                done_ts = shell_ts
        week_label, _ = _week_info(done_ts)
        done_by_week[week_label].add(ex.issue)
        state = issues[ex.issue]
        has_rework = bool(state.reruns or state.repairs or state.humans)
        if not has_rework:
            first_pass_by_week[week_label].add(ex.issue)

    active_by_week: dict[str, set[int]] = defaultdict(set)
    submitted_by_week: dict[str, set[int]] = defaultdict(set)
    first_start: dict[int, datetime] = {}
    for ex in all_executions:
        week_label, _ = _week_info(ex.ts)
        active_by_week[week_label].add(ex.issue)
        if ex.issue not in first_start or ex.ts < first_start[ex.issue]:
            first_start[ex.issue] = ex.ts
    for issue, ts in first_start.items():
        week_label, _ = _week_info(ts)
        submitted_by_week[week_label].add(issue)

    andon_by_week: dict[str, int] = defaultdict(int)
    for row in andon_raised_rows:
        ts = row["_parsed_ts"]
        week_label, _ = _week_info(ts)
        andon_by_week[week_label] += 1

    week_keys = sorted(
        set(active_by_week) | set(done_by_week) | {item.week for item in rework_items + noise_items},
        key=lambda w: (int(w.split("-W")[0]), int(w.split("-W")[1])),
    )

    weeks_out: list[dict[str, Any]] = []
    for week_label in week_keys:
        start_monday = None
        for ex in all_executions:
            wl, monday = _week_info(ex.ts)
            if wl == week_label:
                start_monday = monday
                break
        if start_monday is None:
            year, week_num = week_label.split("-W")
            start_monday = date.fromisocalendar(int(year), int(week_num), 1).isoformat()

        rework_week = [i for i in rework_items if i.week == week_label]
        noise_week = [i for i in noise_items if i.week == week_label]
        rework_counts = {
            "rerun": sum(1 for i in rework_week if i.kind == "rerun"),
            "repair": sum(1 for i in rework_week if i.kind == "repair"),
            "human": sum(1 for i in rework_week if i.kind == "human"),
        }
        active_issues = len(active_by_week.get(week_label, set()))
        done_issues = len(done_by_week.get(week_label, set()))
        first_pass_issues = len(first_pass_by_week.get(week_label, set()))
        total_rework = sum(rework_counts.values())
        q2 = (total_rework / active_issues) if active_issues else 0.0
        submitted = len(submitted_by_week.get(week_label, set()))
        andon_raised = andon_by_week.get(week_label, 0)
        q3 = (andon_raised / submitted) if submitted else 0.0

        cost_usd = week_cost.get(week_label, 0.0)
        unpriced_calls = week_unpriced_calls.get(week_label, 0)
        unpriced_tokens = week_unpriced_tokens.get(week_label, 0)
        q4 = (cost_usd / active_issues) if active_issues else 0.0

        by_step: dict[str, dict[str, int]] = defaultdict(
            lambda: {"runs": 0, "rerun": 0, "repair": 0, "human": 0, "noise": 0}
        )
        seen_runs: dict[str, set[str]] = defaultdict(set)
        for ex in all_executions:
            wl, _ = _week_info(ex.ts)
            if wl != week_label:
                continue
            if ex.parent_uuid not in seen_runs[ex.step]:
                seen_runs[ex.step].add(ex.parent_uuid)
                by_step[ex.step]["runs"] += 1
        for item in rework_week + noise_week:
            bucket = by_step[item.step]
            if item.kind == "noise":
                bucket["noise"] += 1
            else:
                bucket[item.kind] += 1

        cause_counts: dict[str, dict[str, Any]] = {}
        for item in rework_week:
            entry = cause_counts.setdefault(
                item.cause,
                {"count": 0, "issues": set()},
            )
            entry["count"] += 1
            entry["issues"].add(item.issue)
        by_cause = sorted(
            [
                {
                    "cause": cause,
                    "target": resolve_cause_target(cause, config.metrics.cause_targets),
                    "count": data["count"],
                    "issues": sorted(data["issues"]),
                }
                for cause, data in cause_counts.items()
            ],
            key=lambda row: (-row["count"], row["cause"]),
        )

        weeks_out.append(
            {
                "week": week_label,
                "start": start_monday,
                "q1_first_pass_rate": (first_pass_issues / done_issues) if done_issues else None,
                "done_issues": done_issues,
                "first_pass_issues": first_pass_issues,
                "q2_rework_per_issue": q2,
                "active_issues": active_issues,
                "rework": rework_counts,
                "noise": len(noise_week),
                "q3_andon_rate": q3,
                "andon_raised": andon_raised,
                "submitted_issues": submitted,
                "q4_cost_per_issue_usd": q4,
                "cost_usd": cost_usd,
                "unpriced": {"calls": unpriced_calls, "tokens": unpriced_tokens},
                "by_step": dict(by_step),
                "by_cause": by_cause,
            }
        )

    issues_out = []
    for issue_num in sorted(issues):
        state = issues[issue_num]
        issues_out.append(
            {
                "issue": issue_num,
                "runs": dict(sorted(state.runs_by_step.items())),
                "rework": [
                    {
                        "kind": item.kind,
                        "step": item.step,
                        "origin_step": item.origin_step,
                        "cause": item.cause,
                        "ts": item.ts,
                    }
                    for item in sorted(
                        state.reruns + state.repairs + state.humans,
                        key=lambda i: i.ts,
                    )
                ],
                "cost_usd": state.cost_usd,
            }
        )

    since_str = since_dt.date().isoformat() if since_dt is not None else None
    return {
        "since": since_str,
        "timezone": config.timezone,
        "skipped_rows": skipped_rows,
        "weeks": weeks_out,
        "issues": issues_out,
    }


def format_text(result: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for week in result.get("weeks") or []:
        lines.append(f"Week {week['week']} (from {week['start']})")
        q1 = week.get("q1_first_pass_rate")
        q1_text = f"{q1:.0%}" if q1 is not None else "n/a"
        lines.append(f"  Q1 first-pass rate: {q1_text}")
        lines.append(f"  Q2 rework / issue: {week.get('q2_rework_per_issue', 0):.2f}")
        lines.append(f"  Q3 andon rate: {week.get('q3_andon_rate', 0):.2%}")
        q4 = week.get("q4_cost_per_issue_usd")
        lines.append(f"  Q4 cost / issue (USD): {q4:.2f}" if q4 is not None else "  Q4 cost / issue (USD): n/a")
        lines.append(
            "  Rework:"
            f" rerun={week.get('rework', {}).get('rerun', 0)}"
            f" repair={week.get('rework', {}).get('repair', 0)}"
            f" human={week.get('rework', {}).get('human', 0)}"
            f" noise={week.get('noise', 0)}"
        )
        lines.append("  Top causes:")
        for row in (week.get("by_cause") or [])[:5]:
            target = row.get("target")
            target_text = f" -> {target}" if target else ""
            lines.append(f"    {row['cause']}: {row['count']}{target_text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="issuesmith metrics rework")
    parser.add_argument("--since", help="Include rows on/after this date (YYYY-MM-DD)")
    parser.add_argument("--issue", type=int, help="Filter to one issue number")
    parser.add_argument("--audit", type=Path, help="ghdag audit JSONL path")
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON output")
    args = parser.parse_args(argv)

    from issuesmith.config import get_config

    cfg = get_config()
    metrics_path = cfg.paths.metrics
    if not metrics_path.exists():
        print(f"metrics rework: metrics file not found: {metrics_path}", file=sys.stderr)
        return 1

    since_dt: datetime | None = None
    if args.since:
        try:
            since_dt = parse_since(args.since, ZoneInfo(cfg.timezone))
        except ValueError as exc:
            print(f"metrics rework: {exc}", file=sys.stderr)
            return 1

    result = compute(
        metrics_path,
        audit_path=args.audit,
        config=cfg,
        since=since_dt,
        issue_filter=args.issue,
    )
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(format_text(result))
    return 0


__all__ = ["compute", "format_text", "load_jsonl", "main", "parse_since", "resolve_cause_target"]
