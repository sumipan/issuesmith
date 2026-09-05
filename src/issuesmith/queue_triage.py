"""Deterministic gates and LLM triage for issuesmith queue."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from ghdag.llm import call_text
from packaging.version import InvalidVersion, Version

from issuesmith.queue_store import (
    DEFAULT_SEED_PATH,
    DEFAULT_TRIAGE_LOG_PATH,
    PRIORITY_RANK,
    QueueRequest,
    QueueSnapshot,
    QueueStore,
)

DecisionKind = Literal[
    "keep",
    "closed",
    "already_processed",
    "superseded",
    "rejected",
    "duplicate",
]

LLMDecision = Literal["keep", "reject"]

READY_LABEL = {
    "draft": "issuesmith:draft-ready",
    "develop": "issuesmith:develop-ready",
    "merge": "issuesmith:merge-ready",
}
RUNNING_LABEL = {
    "draft": "issuesmith:draft-running",
    "develop": "issuesmith:develop-running",
    "merge": "issuesmith:merge-running",
}
DONE_LABEL = {
    "draft": "issuesmith:draft-done",
    "develop": "issuesmith:develop-done",
    "merge": "issuesmith:merge-done",
}

# Closed without merge-done but still a valid pipeline terminal (#2825).
TERMINAL_WITHOUT_MERGE = {
    "issuesmith:sub-ready",
    "issuesmith:sub-done",
    "issuesmith:rejected",
    "issuesmith:superseded",
}

_BUMP_RE = re.compile(
    r"^(?P<prefix>.+?):\s*bump\s+(?P<dep>\S+)\s+to\s+v?(?P<ver>\d+\.\d+\.\d+)\s*$",
    re.IGNORECASE,
)
_README_RE = re.compile(
    r"^(?P<repo>\S+):\s*v?(?P<ver>\d+\.\d+\.\d+)\s+に合わせて\s*README\.md\s*を書き直す\s*$",
)
_SEMVER_RE = re.compile(r"v?(\d+\.\d+\.\d+)")

COMMENT_MARKER = "<!-- ISSUESMITH_QUEUE_REQUEST:{request_id}:{outcome} -->"


@dataclass
class Decision:
    kind: DecisionKind
    reason: str
    close_issue: bool = False
    comment: bool = True
    add_rejected_label: bool = False


@dataclass
class TriageDecision:
    request_id: str
    decision: LLMDecision
    reason: str
    uncertain_flag: bool = False


@dataclass
class TriageResult:
    order: list[str]
    decisions: list[TriageDecision]
    adopted: bool
    fallback_reason: str | None
    raw_output: str | None
    uncertain_flag: bool
    latency_ms: int
    trace_id: str
    prompt_version: str


def label_names(issue: dict[str, Any]) -> set[str]:
    labels = issue.get("labels") or []
    names: set[str] = set()
    if not isinstance(labels, list):
        return names
    for item in labels:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                names.add(name)
        elif isinstance(item, str):
            names.add(item)
    return names


def normalize_similar_title(title: str) -> tuple[str, Version] | None:
    """Normalize bump / README rewrite titles for same-kind comparison.

    Returns (kind_key, version) or None if title does not match known forms.
    """
    title = title.strip()
    m = _BUMP_RE.match(title)
    if m:
        try:
            ver = Version(m.group("ver"))
        except InvalidVersion:
            return None
        kind = f"bump|{m.group('prefix').strip().lower()}|{m.group('dep').strip().lower()}"
        return kind, ver
    m = _README_RE.match(title)
    if m:
        try:
            ver = Version(m.group("ver"))
        except InvalidVersion:
            return None
        kind = f"readme|{m.group('repo').strip().lower()}"
        return kind, ver
    return None


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


def missing_yaml_fields(body: str) -> list[str]:
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


def comment_marker(request_id: str, outcome: str) -> str:
    return COMMENT_MARKER.format(request_id=request_id, outcome=outcome)


def has_marker_comment(comments: list[dict[str, Any]], request_id: str, outcome: str) -> bool:
    needle = comment_marker(request_id, outcome)
    for c in comments:
        body = c.get("body") if isinstance(c, dict) else None
        if isinstance(body, str) and needle in body:
            return True
    return False


def deterministic_decision(
    request: QueueRequest,
    issue: dict[str, Any],
    *,
    open_issues: list[dict[str, Any]] | None = None,
    force: bool = False,
) -> Decision:
    state = str(issue.get("state", "")).upper()
    labels = label_names(issue)
    phase = request.phase
    done = DONE_LABEL[phase]

    if state == "CLOSED":
        # force=True かつフェーズ未完了 → keep に進む（PR 自動クローズ後の merge 復旧）。
        if force and done not in labels:
            pass
        else:
            return Decision(kind="closed", reason=f"issue #{request.issue} is CLOSED", comment=True)

    ready = READY_LABEL[phase]
    running = RUNNING_LABEL[phase]
    if ready in labels:
        return Decision(
            kind="already_processed",
            reason=f"{ready} already present",
            comment=False,
        )
    if running in labels:
        return Decision(
            kind="already_processed",
            reason=f"{running} already present",
            comment=True,
        )
    if done in labels and not force:
        return Decision(
            kind="already_processed",
            reason=f"{done} already present",
            comment=True,
        )
    # force=True: done ラベルが付いていても再ディスパッチを許可する。
    # CP2 FAIL 等でフェーズが *-done に差し戻された Issue を、案内された
    # `enqueue --force` で再投入する経路（2026-09-04 に発覚した回帰）。
    # ready（既に投入中）は force でも bypass しない — #2813 の二重投入
    # レース対策と矛盾するため。

    # Same-kind newer SemVer supersedes.
    title = str(issue.get("title") or "")
    self_norm = normalize_similar_title(title)
    if self_norm and open_issues:
        kind, self_ver = self_norm
        newer: list[tuple[int, Version]] = []
        for other in open_issues:
            other_num = other.get("number")
            if not isinstance(other_num, int) or other_num == request.issue:
                continue
            other_state = str(other.get("state", "")).upper()
            if other_state not in ("OPEN", ""):
                continue
            other_norm = normalize_similar_title(str(other.get("title") or ""))
            if not other_norm:
                continue
            other_kind, other_ver = other_norm
            if other_kind == kind and other_ver > self_ver:
                newer.append((other_num, other_ver))
        if newer:
            newer.sort(key=lambda x: x[1], reverse=True)
            top_num, top_ver = newer[0]
            return Decision(
                kind="superseded",
                reason=f"superseded by #{top_num} (v{top_ver})",
                close_issue=True,
                comment=True,
            )

    missing = missing_yaml_fields(str(issue.get("body") or ""))
    if missing:
        return Decision(
            kind="rejected",
            reason=f"missing YAML fields: {', '.join(missing)}",
            comment=True,
            add_rejected_label=True,
        )

    return Decision(kind="keep", reason="ok", comment=False)


def deterministic_order(requests: list[QueueRequest]) -> list[str]:
    def key(req: QueueRequest) -> tuple[int, str, str]:
        return (PRIORITY_RANK.get(req.priority, 99), req.requested_at, req.request_id)

    return [r.request_id for r in sorted(requests, key=key)]


def apply_deterministic_order_constraints(
    order: list[str],
    requests: dict[str, QueueRequest],
    *,
    now: datetime | None = None,
) -> list[str]:
    """Re-apply hard constraints after LLM order: high before others; wait>=7d first within priority.

    Within the same (priority, aging) bucket the LLM relative order is preserved.
    Do not re-sort by requested_at / request_id — that would erase a valid LLM permutation.
    """
    now = now or datetime.now(timezone.utc)
    reqs = [requests[rid] for rid in order if rid in requests]

    def wait_seconds(req: QueueRequest) -> float:
        try:
            at = datetime.fromisoformat(req.requested_at)
        except ValueError:
            return 0.0
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        return max(0.0, (now - at).total_seconds())

    # Stable: LLM relative order within the same constraint key via original index.
    index = {rid: i for i, rid in enumerate(order)}

    def full_key(req: QueueRequest) -> tuple[int, int, int]:
        wait = wait_seconds(req)
        aged = 0 if wait >= 7 * 24 * 3600 else 1
        return (PRIORITY_RANK.get(req.priority, 99), aged, index.get(req.request_id, 0))

    return [r.request_id for r in sorted(reqs, key=full_key)]


def _git_prompt_version() -> str:
    try:
        import subprocess

        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()[:12]
    except Exception:
        return "unknown"


def _parse_llm_response(raw: str, expected_ids: set[str]) -> tuple[list[str], list[TriageDecision]]:
    text = raw.strip()
    # Allow fenced JSON.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM response must be object")
    order = data.get("order")
    decisions = data.get("decisions")
    if not isinstance(order, list) or not isinstance(decisions, list):
        raise ValueError("order/decisions must be lists")
    if set(order) != expected_ids or len(order) != len(expected_ids):
        raise ValueError("order must be a permutation of request ids")
    seen: set[str] = set()
    parsed: list[TriageDecision] = []
    for item in decisions:
        if not isinstance(item, dict):
            raise ValueError("decision must be object")
        rid = item.get("request_id")
        decision = item.get("decision")
        reason = item.get("reason")
        uncertain = item.get("uncertain_flag")
        if rid not in expected_ids or rid in seen:
            raise ValueError("invalid or duplicate request_id in decisions")
        if decision not in ("keep", "reject"):
            raise ValueError("decision must be keep|reject")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be non-empty")
        if not isinstance(uncertain, bool):
            raise ValueError("uncertain_flag must be bool")
        seen.add(rid)
        parsed.append(
            TriageDecision(
                request_id=str(rid),
                decision=decision,  # type: ignore[arg-type]
                reason=reason.strip(),
                uncertain_flag=uncertain,
            )
        )
    if seen != expected_ids:
        raise ValueError("decisions must cover all request ids exactly once")
    return [str(x) for x in order], parsed


def append_triage_log(
    entry: dict[str, Any],
    *,
    path: Path | None = None,
) -> None:
    log_path = path or DEFAULT_TRIAGE_LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def triage(
    snapshot: QueueSnapshot,
    *,
    issues: dict[int, dict[str, Any]],
    store: QueueStore | None = None,
    call_llm: Callable[..., Any] | None = None,
    now: datetime | None = None,
    triage_log_path: Path | None = None,
    timeout: int = 60,
    engine: str = "claude",
    model: str = "claude-sonnet-4-6",
) -> TriageResult:
    """Run LLM triage when revision advanced; always write audit log."""
    now = now or datetime.now(timezone.utc)
    active = []
    for rid in snapshot.active_order:
        if store is not None:
            req = store.effective_request(snapshot, rid)
        else:
            req = snapshot.requests.get(rid)
        if req is not None:
            active.append(req)

    expected_ids = {r.request_id for r in active}
    fallback = deterministic_order(active)
    prompt_version = _git_prompt_version()
    trace_id = str(uuid.uuid4())
    started = time.monotonic()

    if snapshot.revision <= snapshot.last_triaged_revision or not active:
        result = TriageResult(
            order=list(snapshot.active_order),
            decisions=[TriageDecision(rid, "keep", "no triage needed") for rid in snapshot.active_order],
            adopted=False,
            fallback_reason="revision unchanged or empty",
            raw_output=None,
            uncertain_flag=False,
            latency_ms=0,
            trace_id=trace_id,
            prompt_version=prompt_version,
        )
        return result

    payload_requests = []
    for req in active:
        issue = issues.get(req.issue) or {}
        try:
            at = datetime.fromisoformat(req.requested_at)
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            wait_s = max(0, int((now - at).total_seconds()))
        except ValueError:
            wait_s = 0
        body = str(issue.get("body") or "")[:500]
        payload_requests.append(
            {
                "request_id": req.request_id,
                "issue": req.issue,
                "phase": req.phase,
                "source": req.source,
                "actor_kind": req.actor_kind,
                "priority": req.priority,
                "requested_at": req.requested_at,
                "requested_by": list(req.requested_by),
                "title": issue.get("title"),
                "body_head": body,
                "labels": sorted(label_names(issue)),
                "wait_seconds": wait_s,
            }
        )

    prompt = (
        "You reorder and optionally reject automation jobs for issuesmith.\n"
        "Return ONLY a JSON object with keys order and decisions.\n"
        "order must be a permutation of all request_id values.\n"
        "Each decision: request_id, decision(keep|reject), reason(non-empty), uncertain_flag(bool).\n"
        f"requests={json.dumps(payload_requests, ensure_ascii=False)}\n"
    )

    raw_output: str | None = None
    fallback_reason: str | None = None
    adopted = False
    order = fallback
    decisions: list[TriageDecision] = [
        TriageDecision(rid, "keep", "deterministic fallback") for rid in fallback
    ]
    uncertain_flag = False
    token_usage: Any = None

    llm = call_llm
    if llm is None:
        llm = call_text

    if llm is not None:
        try:
            from ghdag.llm import EngineModelError, validate_engine_model

            validate_engine_model(engine, model)
        except EngineModelError as exc:
            fallback_reason = f"config error: {exc}"
            llm = None
        except Exception as exc:  # pragma: no cover — import / unexpected validation failures
            fallback_reason = f"config error: {exc}"
            llm = None

    if llm is not None:
        try:
            result = llm(prompt, engine=engine, model=model, timeout=timeout)
            raw_output = (
                getattr(result, "text", None)
                or getattr(result, "body", None)
                or str(result)
            )
            token_usage = getattr(result, "usage", None)
            parsed_order, parsed_decisions = _parse_llm_response(raw_output, expected_ids)
            # Protect human / force from LLM reject.
            protected: set[str] = set()
            for req in active:
                force = store.is_force(snapshot, req.request_id) if store else False
                if req.actor_kind == "human" or force:
                    protected.add(req.request_id)
            fixed: list[TriageDecision] = []
            for d in parsed_decisions:
                if d.decision == "reject" and d.request_id in protected:
                    fixed.append(
                        TriageDecision(
                            request_id=d.request_id,
                            decision="keep",
                            reason=f"LLM reject ignored for protected request: {d.reason}",
                            uncertain_flag=d.uncertain_flag,
                        )
                    )
                else:
                    fixed.append(d)
            req_map = {r.request_id: r for r in active}
            order = apply_deterministic_order_constraints(parsed_order, req_map, now=now)
            decisions = fixed
            adopted = True
            uncertain_flag = any(d.uncertain_flag for d in fixed)
        except Exception as exc:
            fallback_reason = f"llm failed: {exc}"
            order = fallback
            decisions = [TriageDecision(rid, "keep", "deterministic fallback") for rid in fallback]
            adopted = False

    latency_ms = int((time.monotonic() - started) * 1000)
    entry = {
        "input": {"requests": payload_requests, "prompt": prompt},
        "output": {
            "raw": raw_output,
            "parsed": {
                "order": order,
                "decisions": [
                    {
                        "request_id": d.request_id,
                        "decision": d.decision,
                        "reason": d.reason,
                        "uncertain_flag": d.uncertain_flag,
                    }
                    for d in decisions
                ],
            },
        },
        "reasoning": [d.reason for d in decisions],
        "config": {
            "engine": engine,
            "model": model,
            "prompt_version": prompt_version,
        },
        "metadata": {
            "trace_id": trace_id,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
            "queue_revision": snapshot.revision,
        },
        "uncertain_flag": uncertain_flag,
        "adopted_order": order if adopted else None,
        "fallback_reason": fallback_reason,
    }
    append_triage_log(entry, path=triage_log_path)

    return TriageResult(
        order=order,
        decisions=decisions,
        adopted=adopted,
        fallback_reason=fallback_reason,
        raw_output=raw_output,
        uncertain_flag=uncertain_flag,
        latency_ms=latency_ms,
        trace_id=trace_id,
        prompt_version=prompt_version,
    )


def load_seed_entries(path: Path | None = None) -> list[dict[str, Any]]:
    seed_path = path or DEFAULT_SEED_PATH
    if not seed_path.exists():
        return []
    data = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return []
    queue = data.get("queue") or []
    if not isinstance(queue, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in queue:
        if not isinstance(item, dict):
            continue
        issue = item.get("issue")
        label = str(item.get("label") or "")
        if not isinstance(issue, int) or issue <= 0:
            continue
        phase = "draft"
        if label.endswith("develop-ready"):
            phase = "develop"
        elif label.endswith("merge-ready"):
            phase = "merge"
        entries.append(
            {
                "issue": issue,
                "phase": phase,
                "label": label,
                "seed_key": f"seed:{issue}:{phase}",
            }
        )
    return entries


def seed_window(path: Path | None = None) -> tuple[str, str, int]:
    seed_path = path or DEFAULT_SEED_PATH
    start, end, idle = "01:00", "07:00", 5
    if not seed_path.exists():
        return start, end, idle
    data = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return start, end, idle
    window = data.get("window") if isinstance(data.get("window"), dict) else {}
    start = str(window.get("start", start))
    end = str(window.get("end", end))
    idle = int(data.get("idle_minutes", idle))
    return start, end, idle
