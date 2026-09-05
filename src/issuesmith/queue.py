"""issuesmith queue CLI — enqueue, triage, dispatch, migrate."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from ghdag.core.exceptions import GitHubApiError
from ghdag.github_client import GitHubClient
from ghdag.quota import QuotaGate

from issuesmith.config import get_config
from issuesmith.queue_store import (
    DEFAULT_NIGHT_STATE_PATH,
    DEFAULT_SEED_PATH,
    DEFAULT_TRIAGE_LOG_PATH,
    QueueStore,
    QueueValidationError,
)
from issuesmith.queue_triage import (
    DONE_LABEL,
    READY_LABEL,
    RUNNING_LABEL,
    TERMINAL_WITHOUT_MERGE,
    comment_marker,
    deterministic_decision,
    has_marker_comment,
    label_names,
    load_seed_entries,
    seed_window,
    triage,
)

_cfg = get_config()
TZ = ZoneInfo(_cfg.timezone)
REPO = _cfg.repo

REPO_ROOT = _cfg.root
JOBS_DIR = _cfg.paths.exec_jsonl.parent
DONE_DIR = _cfg.paths.done_dir
EXEC_PATH = _cfg.paths.exec_jsonl
QUOTA_STATE_PATH = _cfg.paths.quota_state


@dataclass
class DispatchResult:
    dispatched: bool
    issue: int | None = None
    label: str | None = None
    request_id: str | None = None
    reason: str = ""


def _now_jst() -> datetime:
    return datetime.now(TZ)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def _in_window(now: datetime, start_str: str, end_str: str) -> bool:
    start = _parse_hhmm(start_str)
    end = _parse_hhmm(end_str)
    current = now.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _iter_issuesmith_exec_uuids() -> list[str]:
    if not EXEC_PATH.exists():
        return []
    uuids: list[str] = []
    for raw in EXEC_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        idempotency_key = str(row.get("idempotency_key", ""))
        uuid = row.get("uuid")
        if idempotency_key.startswith("issuesmith:") and isinstance(uuid, str) and uuid:
            uuids.append(uuid)
    return uuids


def _latest_done_mtime() -> float | None:
    if not DONE_DIR.exists():
        return None
    mtimes = [p.stat().st_mtime for p in DONE_DIR.iterdir() if p.is_file()]
    if not mtimes:
        return None
    return max(mtimes)


def _pipeline_idle_enough(idle_minutes: int, now: datetime) -> bool:
    uuids = _iter_issuesmith_exec_uuids()
    for uuid in uuids:
        if not (DONE_DIR / uuid).exists():
            return False
    latest = _latest_done_mtime()
    if latest is None:
        return False
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


ENGINE_STATE_PATH = _cfg.paths.engine_state
_DEFAULT_ROLE_ENGINES = {"design": "claude", "implementation": "claude"}


def _required_engines(engine_state_path: Path | None = None) -> dict[str, str]:
    """role → engine を .pipeline-state/issuesmith-engine.yml から読む。

    読めない場合は既定（design/implementation とも claude）に倒す。
    """
    path = engine_state_path or ENGINE_STATE_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return dict(_DEFAULT_ROLE_ENGINES)
    result: dict[str, str] = {}
    for role, default in _DEFAULT_ROLE_ENGINES.items():
        value = data.get(role) if isinstance(data, dict) else None
        engine = value.get("engine") if isinstance(value, dict) else None
        result[role] = str(engine) if engine else default
    return result


def _required_engines_paused(
    quota_path: Path | None = None,
    engine_state_path: Path | None = None,
) -> list[str]:
    """投入に必要なロール（design / implementation）の engine のうち paused なものを返す。

    旧実装は「登録済み engine が全部 paused」で止めていたが、フォールバック先
    （codex 等）だけが paused でも全体が止まり、逆に design engine が paused でも
    他が空いていれば投入が続いてしまった（2026-09-04）。必要ロールの engine で判定する。
    """
    snapshot = QuotaGate(state_path=quota_path or QUOTA_STATE_PATH).snapshot()
    if not snapshot.engines:
        return []
    required = set(_required_engines(engine_state_path).values())
    return sorted(
        name for name, engine in snapshot.engines.items()
        if name in required and engine.status == "paused"
    )


def _all_engines_paused(quota_path: Path | None = None) -> bool:
    """後方互換ラッパ。必要ロールの engine が 1 つでも paused なら True。"""
    return bool(_required_engines_paused(quota_path))


def _source_in_window(source: str, actor_kind: str, now: datetime, start: str, end: str) -> bool:
    if actor_kind == "human":
        return True
    if source in ("release-watcher", "night-queue-seed", "night-queue"):
        return _in_window(now, start, end)
    # Other automation: allow always unless configured otherwise.
    return True


def _any_in_flight_elsewhere(client: GitHubClient, issue_number: int) -> bool:
    """True if another open Issue holds -ready or -running (already dispatched / in progress)."""
    for label in (
        READY_LABEL["draft"],
        RUNNING_LABEL["draft"],
        READY_LABEL["develop"],
        RUNNING_LABEL["develop"],
        READY_LABEL["merge"],
        RUNNING_LABEL["merge"],
    ):
        try:
            issues = client.list_issues(label, state="open")
        except Exception:
            continue
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            num = issue.get("number")
            if isinstance(num, int) and num != issue_number:
                return True
    return False


def _closes_issue_marker(issue_number: int) -> re.Pattern[str]:
    # Digit boundary: avoid Closes #12 matching #123
    return re.compile(rf"(?i)\bcloses\s+#{issue_number}(?!\d)")


def _find_open_prs_closing_issue(client: GitHubClient, issue_number: int) -> list[dict[str, Any]]:
    """Find open PRs whose title/body contain ``Closes #N``.

    Production ``GitHubClient.pr_list(search=...)`` only filters title/head, and
    ``_normalize_prs`` strips ``body``. Mirror loops_host: list open PRs, then
    ``pr_get`` for each candidate to read the body.
    """
    marker = _closes_issue_marker(issue_number)
    try:
        prs = client.pr_list(state="open", limit=100)
    except Exception:
        return []
    if not isinstance(prs, list):
        return []
    matched: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        if marker.search(title) or marker.search(body):
            matched.append(pr)
            continue
        number = pr.get("number")
        if not isinstance(number, int):
            continue
        try:
            detail = client.pr_get(number)
        except Exception:
            continue
        if not isinstance(detail, dict):
            continue
        d_title = str(detail.get("title") or "")
        d_body = str(detail.get("body") or "")
        if marker.search(d_title) or marker.search(d_body):
            matched.append(detail)
    return matched


def _pr_is_merged(pr: dict[str, Any]) -> bool:
    """True if PR dict looks merged (GraphQL MERGED, REST merged_at, or merged flag)."""
    if pr.get("merged") is True:
        return True
    for key in ("mergedAt", "merged_at"):
        val = pr.get(key)
        if isinstance(val, str) and val.strip():
            return True
    return str(pr.get("state") or "").upper() == "MERGED"


def _find_merged_prs_closing_issue(client: GitHubClient, issue_number: int) -> list[dict[str, Any]]:
    """Find merged PRs whose title/body contain ``Closes #N``.

    ``pr_list(state="closed")`` strips ``body`` and often omits merge metadata.
    Use ``pr_get`` for body and ``api_request("pulls/{n}")`` when merge status
    is missing from the normalized list (production GitHubClient).
    """
    marker = _closes_issue_marker(issue_number)
    try:
        prs = client.pr_list(state="closed", limit=100)
    except Exception:
        return []
    if not isinstance(prs, list):
        return []
    matched: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if not isinstance(number, int):
            continue

        detail: dict[str, Any] = dict(pr)
        if not _pr_is_merged(detail):
            try:
                raw = client.api_request(f"pulls/{number}")
            except Exception:
                raw = None
            if isinstance(raw, dict) and _pr_is_merged(raw):
                detail = {**detail, **raw}
            else:
                continue

        title = str(detail.get("title") or "")
        body = str(detail.get("body") or "")
        if marker.search(title) or marker.search(body):
            matched.append(detail)
            continue
        try:
            fetched = client.pr_get(number)
        except Exception:
            continue
        if not isinstance(fetched, dict):
            continue
        d_title = str(fetched.get("title") or "")
        d_body = str(fetched.get("body") or "")
        if marker.search(d_title) or marker.search(d_body):
            matched.append({**detail, **fetched})
    return matched


def _list_open_issues(client: GitHubClient) -> list[dict[str, Any]]:
    """List all open Issues (excluding PRs) via the production GitHubClient contract.

    ``list_issues`` requires a label and cannot scan the whole repo for supersede.
    Use ``api_request("issues?state=open&...")`` which returns raw GitHub JSON.
    """
    raw: Any = None
    try:
        raw = client.api_request("issues?state=open&per_page=100", paginate=True)
    except Exception:
        try:
            raw = client.api_request("issues?state=open&per_page=100")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        # GitHub /issues includes pull requests; skip them for supersede.
        if item.get("pull_request") is not None:
            continue
        state = str(item.get("state") or "OPEN").upper()
        out.append(
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "state": state,
                "body": item.get("body") or "",
                "labels": item.get("labels") or [],
            }
        )
    return out


_BRUSHUP_STEPS = frozenset({"b1", "cp1_gate", "cp1"})
_IMPL_STEPS = frozenset({"p0", "p1", "p2", "p2r", "p3", "cp2", "m1", "m1r", "m2"})


def handler_for_failed_step(failed_step: str, labels: set[str]) -> str:
    if failed_step in _BRUSHUP_STEPS:
        return "brushup"
    if failed_step in {"m1", "m1r", "m2"} and (
        RUNNING_LABEL["merge"] in labels or READY_LABEL["merge"] in labels
    ):
        return "merge"
    return "impl"


def infer_redispatch_phase(failed_step: str, labels: set[str]) -> str:
    if failed_step in _BRUSHUP_STEPS or DONE_LABEL["draft"] not in labels and (
        READY_LABEL["draft"] in labels or RUNNING_LABEL["draft"] in labels
    ):
        return "draft"
    if RUNNING_LABEL["merge"] in labels or READY_LABEL["merge"] in labels:
        return "merge"
    if failed_step in _IMPL_STEPS:
        return "develop"
    return "develop"


def redispatch_label_plan(phase: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (labels that must be present, labels to remove) for redispatch."""
    if phase == "draft":
        return frozenset(), frozenset(
            {READY_LABEL["draft"], RUNNING_LABEL["draft"], DONE_LABEL["draft"]}
        )
    if phase == "develop":
        return frozenset({DONE_LABEL["draft"]}), frozenset(
            {
                READY_LABEL["develop"],
                RUNNING_LABEL["develop"],
                DONE_LABEL["develop"],
            }
        )
    if phase == "merge":
        return frozenset(), frozenset(
            {READY_LABEL["merge"], RUNNING_LABEL["merge"], DONE_LABEL["merge"]}
        )
    raise ValueError(f"unknown phase {phase}")


def apply_redispatch_labels(
    client: GitHubClient,
    issue_number: int,
    phase: str,
    current_labels: set[str],
) -> bool:
    required, to_remove = redispatch_label_plan(phase)
    labels_add = sorted(lab for lab in required if lab not in current_labels)
    labels_remove = sorted(lab for lab in to_remove if lab in current_labels)
    if not labels_add and not labels_remove:
        return False
    client.issue_update(issue_number, labels_add=labels_add, labels_remove=labels_remove)
    return True


def phase_preconditions(
    phase: str, issue: dict[str, Any], client: GitHubClient, issue_number: int
) -> tuple[bool, str]:
    return _phase_preconditions(phase, issue, client, issue_number)


def _phase_preconditions(phase: str, issue: dict[str, Any], client: GitHubClient, issue_number: int) -> tuple[bool, str]:
    state = str(issue.get("state", "")).upper()
    if state != "OPEN":
        return False, "issue not OPEN"
    labels = label_names(issue)
    if phase == "draft":
        for lab in (READY_LABEL["draft"], RUNNING_LABEL["draft"], DONE_LABEL["draft"]):
            if lab in labels:
                return False, f"{lab} present"
        return True, "ok"
    if phase == "develop":
        if DONE_LABEL["draft"] not in labels:
            return False, "draft-done required"
        for lab in (READY_LABEL["develop"], RUNNING_LABEL["develop"], DONE_LABEL["develop"]):
            if lab in labels:
                return False, f"{lab} present"
        return True, "ok"
    if phase == "merge":
        for lab in (READY_LABEL["merge"], RUNNING_LABEL["merge"], DONE_LABEL["merge"]):
            if lab in labels:
                return False, f"{lab} present"
        # Allow label-less recovery after reset.
        matched = _find_open_prs_closing_issue(client, issue_number)
        if matched:
            return True, "ok"
        merged = _find_merged_prs_closing_issue(client, issue_number)
        if merged and DONE_LABEL["merge"] not in labels:
            return True, "already_merged"
        return False, "no open or merged PR with Closes #N"
    return False, f"unknown phase {phase}"


def _ensure_comment(
    client: GitHubClient,
    issue_number: int,
    request_id: str,
    outcome: str,
    body: str,
) -> None:
    try:
        comments = client.get_issue_comments(issue_number)
    except Exception:
        comments = []
    if not isinstance(comments, list):
        comments = []
    if has_marker_comment(comments, request_id, outcome):
        return
    marker = comment_marker(request_id, outcome)
    client.issue_comment(issue_number, f"{body}\n\n{marker}")


def _apply_terminal(
    store: QueueStore,
    client: GitHubClient,
    request_id: str,
    issue_number: int,
    decision_kind: str,
    reason: str,
    *,
    close_issue: bool = False,
    add_rejected_label: bool = False,
    comment: bool = True,
) -> None:
    meta = store.snapshot().request_meta.get(request_id) or {}
    if meta.get("outcome") == decision_kind:
        # Already terminal.
        return
    if comment:
        _ensure_comment(client, issue_number, request_id, decision_kind, reason)
    if add_rejected_label:
        try:
            client.issue_update(issue_number, labels_add=["issuesmith:rejected"], labels_remove=[])
        except Exception as exc:
            print(f"warning: failed to add rejected label: {exc}", file=sys.stderr)
    if decision_kind == "superseded":
        try:
            client.issue_update(issue_number, labels_add=["issuesmith:superseded"], labels_remove=[])
        except Exception as exc:
            print(f"warning: failed to add superseded label: {exc}", file=sys.stderr)
    if close_issue:
        try:
            client.issue_close(issue_number)
        except Exception as exc:
            print(f"warning: failed to close issue: {exc}", file=sys.stderr)
    store.complete(request_id, decision_kind, extra_meta={"reason": reason})


def ensure_seeds_enqueued(store: QueueStore, *, seed_path: Path | None = None, now: datetime | None = None) -> int:
    now = now or _now_jst()
    count = 0
    for entry in load_seed_entries(seed_path):
        result = store.enqueue(
            issue=int(entry["issue"]),
            phase=str(entry["phase"]),
            source="night-queue-seed",
            actor_kind="automation",
            priority="low",
            requested_by=["night-queue"],
            requested_at=now.isoformat(),
            seed_key=str(entry["seed_key"]),
        )
        if result.created:
            count += 1
    return count


def dispatch_one(
    now: datetime | None = None,
    client: GitHubClient | None = None,
    *,
    store: QueueStore | None = None,
    seed_path: Path | None = None,
    call_llm: Any = None,
    skip_seed: bool = False,
) -> DispatchResult:
    now = now or _now_jst()
    store = store or QueueStore()
    client = client or GitHubClient(repo=REPO)
    start, end, idle_minutes = seed_window(seed_path)

    if not skip_seed:
        ensure_seeds_enqueued(store, seed_path=seed_path, now=now)

    snap = store.snapshot()
    if snap.halt:
        return DispatchResult(False, reason=f"halted: {snap.halt_reason}")

    # Fetch issues for active requests.
    issues: dict[int, dict[str, Any]] = {}
    for rid in list(snap.active_order):
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        if req.issue not in issues:
            try:
                data = client.issue_get(
                    req.issue, fields=["state", "labels", "title", "body", "number"]
                )
                data.setdefault("number", req.issue)
                issues[req.issue] = data
            except GitHubApiError as exc:
                if exc.status_code == 404:
                    store.complete(
                        rid,
                        "not_found",
                        extra_meta={"reason": f"issue #{req.issue} does not exist (404)"},
                    )
                    print(f"not_found: #{req.issue} (404)", file=sys.stderr)
                    continue
                print(f"warning: issue_get #{req.issue} failed: {exc}", file=sys.stderr)
                continue
            except Exception as exc:
                print(f"warning: issue_get #{req.issue} failed: {exc}", file=sys.stderr)
                continue

    # Supersede must scan ALL open Issues in the repo, not only those in the queue.
    open_issues = _list_open_issues(client)
    if not open_issues:
        open_issues = [
            iss for iss in issues.values() if str(iss.get("state", "")).upper() == "OPEN"
        ]
    else:
        seen = {i.get("number") for i in open_issues}
        for iss in issues.values():
            if str(iss.get("state", "")).upper() != "OPEN":
                continue
            num = iss.get("number")
            if num not in seen:
                open_issues.append(iss)

    # Deterministic decisions first.
    snap = store.snapshot()
    for rid in list(snap.active_order):
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        issue = issues.get(req.issue)
        if issue is None:
            continue
        decision = deterministic_decision(
            req, issue, open_issues=open_issues, force=store.is_force(snap, rid)
        )
        if decision.kind == "keep":
            continue
        _apply_terminal(
            store,
            client,
            rid,
            req.issue,
            decision.kind,
            decision.reason,
            close_issue=decision.close_issue,
            add_rejected_label=decision.add_rejected_label,
            comment=decision.comment,
        )

    snap = store.snapshot()
    triage_revision = snap.revision
    # Triage if needed (LLM outside lock conceptually — triage() does not hold store lock during LLM).
    if triage_revision > snap.last_triaged_revision and snap.active_order:
        _triage_log = (
            Path(os.environ["ISSUESMITH_QUEUE_DIR"]) / "issuesmith-triage.jsonl"
            if os.environ.get("ISSUESMITH_QUEUE_DIR")
            else DEFAULT_TRIAGE_LOG_PATH
        )
        result = triage(
            snap,
            issues=issues,
            store=store,
            call_llm=call_llm,
            now=now,
            triage_log_path=_triage_log,
        )
        # CAS replace_order first with full permutation of still-active ids.
        active_set = set(snap.active_order)
        new_order = [rid for rid in result.order if rid in active_set]
        for rid in snap.active_order:
            if rid not in new_order:
                new_order.append(rid)
        adopted_cas = store.replace_order(triage_revision, new_order)
        if not adopted_cas:
            store.mark_triaged(triage_revision)
        # Apply LLM rejects after ordering CAS.
        for d in result.decisions:
            if d.decision != "reject":
                continue
            cur = store.snapshot()
            req = store.effective_request(cur, d.request_id)
            if req is None or d.request_id not in cur.active_order:
                continue
            _apply_terminal(
                store,
                client,
                d.request_id,
                req.issue,
                "rejected",
                d.reason,
                close_issue=False,
                add_rejected_label=True,
                comment=True,
            )

    snap = store.snapshot()
    if not snap.active_order:
        return DispatchResult(False, reason="empty queue")

    # Previous issue gate.
    if snap.last_issue is not None:
        try:
            last = client.issue_get(snap.last_issue, fields=["state", "labels"])
        except Exception as exc:
            return DispatchResult(False, reason=f"last_issue fetch failed: {exc}")
        last_labels = label_names(last)
        last_state = str(last.get("state", "")).upper()
        terminal_ok = last_state == "CLOSED" and (
            "issuesmith:merge-done" in last_labels or bool(last_labels & TERMINAL_WITHOUT_MERGE)
        )
        if not terminal_ok:
            if last_state == "OPEN" and _pipeline_idle_enough(idle_minutes, now):
                store.set_halt(
                    True,
                    f"last_issue #{snap.last_issue} is still OPEN while pipeline idle for >= {idle_minutes} minutes",
                )
            elif last_state == "CLOSED":
                store.set_halt(
                    True,
                    f"last_issue #{snap.last_issue} is CLOSED without terminal label (labels: {sorted(last_labels)})",
                )
            return DispatchResult(False, reason="previous issue not terminal")

    if not _pipeline_idle_enough(idle_minutes, now):
        return DispatchResult(False, reason="pipeline not idle")
    paused = _required_engines_paused()
    if paused:
        return DispatchResult(False, reason=f"required engine paused: {', '.join(paused)}")

    # Pick first active that passes gates (exclusive: snapshot → in-flight → label → last_issue).
    with store.dispatch_lock():
        snap = store.snapshot()
        for rid in snap.active_order:
            req = store.effective_request(snap, rid)
            if req is None:
                continue
            if not _source_in_window(req.source, req.actor_kind, now, start, end):
                continue
            try:
                issue = client.issue_get(
                    req.issue, fields=["state", "labels", "title", "body", "number"]
                )
            except Exception:
                continue
            # Re-check deterministic (race).
            decision = deterministic_decision(
                req, issue, open_issues=open_issues, force=store.is_force(snap, rid)
            )
            if decision.kind != "keep":
                _apply_terminal(
                    store,
                    client,
                    rid,
                    req.issue,
                    decision.kind,
                    decision.reason,
                    close_issue=decision.close_issue,
                    add_rejected_label=decision.add_rejected_label,
                    comment=decision.comment,
                )
                continue
            ok, why = _phase_preconditions(req.phase, issue, client, req.issue)
            if not ok:
                # Keep request; do not terminal unless already_processed style.
                continue
            if _any_in_flight_elsewhere(client, req.issue):
                return DispatchResult(False, reason="another issue is running")

            label = READY_LABEL[req.phase]
            labels = label_names(issue)
            if label not in labels:
                client.issue_update(req.issue, labels_add=[label], labels_remove=[])
            _ensure_comment(
                client,
                req.issue,
                rid,
                "dispatched",
                f"issuesmith queue dispatched `{label}` for request `{rid}`.",
            )
            store.complete(rid, "dispatched", extra_meta={"label": label})
            store.set_last_issue(req.issue)
            return DispatchResult(
                True, issue=req.issue, label=label, request_id=rid, reason="dispatched"
            )

    return DispatchResult(False, reason="no dispatchable request")


def _cmd_enqueue(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if args.queue_path else None,
        state_path=Path(args.state_path) if args.state_path else None,
        lock_path=Path(args.lock_path) if args.lock_path else None,
    )
    try:
        result = store.enqueue(
            issue=args.issue,
            phase=args.phase,
            source=args.source,
            actor_kind=args.actor_kind,
            priority=args.priority,
            requested_by=[args.requested_by],
            force=bool(args.force),
        )
    except QueueValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "request_id": result.request_id,
                "created": result.created,
                "merged": result.merged,
                "message": result.message,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_tick(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    result = dispatch_one(store=store, seed_path=Path(args.seed) if getattr(args, "seed", None) else None)
    if result.dispatched:
        print(f"dispatched #{result.issue} {result.label} request={result.request_id}")
    else:
        print(f"no dispatch: {result.reason}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    snap = store.snapshot()
    print(
        "issuesmith-queue status: "
        f"revision={snap.revision} "
        f"active={len(snap.active_order)} "
        f"halt={snap.halt} "
        f"last_issue={snap.last_issue}"
    )
    if snap.halt and snap.halt_reason:
        print(f"  halt_reason: {snap.halt_reason}")
    if snap.last_issue is not None:
        try:
            client = GitHubClient(repo=REPO)
            last = client.issue_get(snap.last_issue, fields=["state", "labels"])
            last_labels = label_names(last)
            last_state = str(last.get("state", "")).upper()
            terminal_ok = last_state == "CLOSED" and (
                "issuesmith:merge-done" in last_labels
                or bool(last_labels & TERMINAL_WITHOUT_MERGE)
            )
            if last_state == "CLOSED" and not terminal_ok:
                print(
                    f"  warning: last_issue #{snap.last_issue} is CLOSED without terminal label "
                    f"(labels: {sorted(last_labels)})"
                )
        except Exception as exc:
            print(f"  warning: last_issue #{snap.last_issue} fetch failed: {exc}", file=sys.stderr)
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if req:
            print(f"  - {rid[:8]}… issue=#{req.issue} phase={req.phase} priority={req.priority} source={req.source}")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    keep_last = bool(getattr(args, "keep_last_issue", False))
    store.clear_halt()
    if keep_last:
        snap = store.snapshot()
        print(f"issuesmith-queue reset: halt=false last_issue={snap.last_issue} (kept)")
    else:
        store.set_last_issue(None)
        print("issuesmith-queue reset: halt=false last_issue=None")
    return 0


def _cmd_skip(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    issue = int(args.issue)
    reason = str(args.reason) if args.reason else ""
    snap = store.snapshot()
    if snap.last_issue is None:
        print("error: no last_issue to skip", file=sys.stderr)
        return 1
    if snap.last_issue != issue:
        print(
            f"error: last_issue is #{snap.last_issue}, not #{issue}",
            file=sys.stderr,
        )
        return 1

    store.set_last_issue(None)
    if snap.halt:
        store.clear_halt()

    client = GitHubClient(repo=REPO)
    try:
        client.issue_get(issue, fields=["number", "state"])
    except GitHubApiError as exc:
        if exc.status_code == 404:
            print(
                f"skip: skipped comment for #{issue} (404 not found)",
                file=sys.stderr,
            )
        else:
            print(
                f"warning: issue_get #{issue} failed during skip: {exc}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"warning: issue_get #{issue} failed during skip: {exc}",
            file=sys.stderr,
        )
    else:
        body = f"issuesmith queue: skipped issue #{issue} from last_issue. reason: {reason}"
        try:
            client.issue_comment(issue, body)
        except Exception as exc:
            print(f"warning: failed to comment on #{issue}: {exc}", file=sys.stderr)

    print(json.dumps({"issue": issue, "outcome": "skipped", "reason": reason}, ensure_ascii=False))
    return 0


def _cmd_dequeue(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    request_id = str(args.request_id)
    reason = str(args.reason) if args.reason else ""
    snap = store.snapshot()
    if request_id not in snap.active_order:
        print(f"error: request {request_id} is not in active_order", file=sys.stderr)
        return 1

    req = store.effective_request(snap, request_id)
    store.complete(request_id, "dequeued", extra_meta={"reason": reason})

    if req is not None:
        client = GitHubClient(repo=REPO)
        try:
            client.issue_get(req.issue, fields=["number", "state"])
        except GitHubApiError as exc:
            if exc.status_code == 404:
                print(
                    f"dequeue: skipped comment for #{req.issue} (404 not found)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"warning: issue_get #{req.issue} failed during dequeue: {exc}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"warning: issue_get #{req.issue} failed during dequeue: {exc}",
                file=sys.stderr,
            )
        else:
            body = f"issuesmith queue: dequeued request {request_id}. reason: {reason}"
            try:
                client.issue_comment(req.issue, body)
            except Exception as exc:
                print(f"warning: failed to comment on #{req.issue}: {exc}", file=sys.stderr)

    print(json.dumps({"request_id": request_id, "outcome": "dequeued", "reason": reason}, ensure_ascii=False))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    snap = store.snapshot()
    active_ids = set(snap.active_order)
    completed = set(snap.completed_request_ids)
    errors: list[str] = []
    if active_ids & completed:
        errors.append("active_order intersects completed_request_ids")
    # Same issue+phase uniqueness
    seen: dict[tuple[int, str], str] = {}
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        key = (req.issue, req.phase)
        if key in seen:
            errors.append(f"duplicate active {key}")
        seen[key] = rid
    if snap.last_triaged_revision > snap.revision:
        errors.append("last_triaged_revision > revision")

    offline = bool(getattr(args, "offline", False))
    if not offline:
        client = GitHubClient(repo=REPO)
        in_flight: set[int] = set()
        for label in (
            READY_LABEL["draft"],
            RUNNING_LABEL["draft"],
            READY_LABEL["develop"],
            RUNNING_LABEL["develop"],
            READY_LABEL["merge"],
            RUNNING_LABEL["merge"],
        ):
            try:
                issues = client.list_issues(label, state="open")
            except Exception as exc:
                print(f"warning: list_issues({label}) failed: {exc}", file=sys.stderr)
                continue
            if not isinstance(issues, list):
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                num = issue.get("number")
                if isinstance(num, int):
                    in_flight.add(num)
        if len(in_flight) >= 2:
            nums = ", ".join(f"#{n}" for n in sorted(in_flight))
            errors.append(f"multiple issues in-flight: {nums}")

    if errors:
        for e in errors:
            print(f"AUDIT FAIL: {e}")
        return 1
    print("AUDIT OK")
    return 0


def _cmd_triage_log(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else DEFAULT_TRIAGE_LOG_PATH
    if not path.exists():
        print("no triage log")
        return 0
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    last_n = lines[-args.last :]
    for ln in last_n:
        print(ln)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    night_state_path = Path(args.from_night_queue_state)
    seed_path = Path(args.seed)
    dry_run = bool(args.dry_run)

    planned: list[dict[str, Any]] = []
    if night_state_path.exists():
        try:
            night = json.loads(night_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            night = {}
        if isinstance(night, dict):
            planned.append(
                {
                    "action": "import_night_state",
                    "last_issue": night.get("last_issue"),
                    "halt": night.get("halt"),
                    "halt_reason": night.get("halt_reason"),
                }
            )

    for entry in load_seed_entries(seed_path):
        planned.append({"action": "enqueue_seed", **entry})

    if dry_run:
        print(json.dumps({"planned": planned}, ensure_ascii=False, indent=2))
        return 0

    if night_state_path.exists():
        try:
            night = json.loads(night_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            night = {}
        if isinstance(night, dict):
            if night.get("last_issue") is not None:
                store.set_last_issue(night.get("last_issue"))
            if night.get("halt") is True:
                store.set_halt(True, night.get("halt_reason"))

    created = ensure_seeds_enqueued(store, seed_path=seed_path)
    snap = store.snapshot()
    print(
        json.dumps(
            {
                "created": created,
                "active": len(snap.active_order),
                "last_issue": snap.last_issue,
                "halt": snap.halt,
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issuesmith.queue")
    parser.add_argument("--queue-path", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--lock-path", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_enq = sub.add_parser("enqueue")
    p_enq.add_argument("--issue", type=int, required=True)
    p_enq.add_argument("--phase", required=True, choices=["draft", "develop", "merge"])
    p_enq.add_argument("--source", required=True)
    p_enq.add_argument("--actor-kind", required=True, choices=["human", "automation"])
    p_enq.add_argument("--priority", required=True, choices=["high", "normal", "low"])
    p_enq.add_argument("--requested-by", required=True)
    p_enq.add_argument("--force", action="store_true")
    p_enq.set_defaults(func=_cmd_enqueue)

    p_tick = sub.add_parser("tick")
    p_tick.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    p_tick.set_defaults(func=_cmd_tick)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=_cmd_status)

    p_reset = sub.add_parser("reset")
    p_reset.add_argument(
        "--keep-last-issue",
        action="store_true",
        help="Clear halt only; keep last_issue (legacy behavior)",
    )
    p_reset.set_defaults(func=_cmd_reset)

    p_skip = sub.add_parser("skip")
    p_skip.add_argument("--issue", type=int, required=True)
    p_skip.add_argument("--reason", default="")
    p_skip.set_defaults(func=_cmd_skip)

    p_deq = sub.add_parser("dequeue")
    p_deq.add_argument("--request-id", required=True)
    p_deq.add_argument("--reason", default="")
    p_deq.set_defaults(func=_cmd_dequeue)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument(
        "--offline",
        action="store_true",
        help="Skip GitHub in-flight checks; local store integrity only",
    )
    p_audit.set_defaults(func=_cmd_audit)

    p_tlog = sub.add_parser("triage-log")
    p_tlog.add_argument("--last", type=int, default=20)
    p_tlog.add_argument("--path", default=None)
    p_tlog.set_defaults(func=_cmd_triage_log)

    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--from-night-queue-state", default=str(DEFAULT_NIGHT_STATE_PATH))
    p_mig.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.set_defaults(func=_cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
