"""labels.py — phase/attention label projection and reconciliation (#3484).

project(issue_number, *, queue_state, exec_records, andon_inbox) computes the
desired label set (pure function).  apply() diffs current vs desired.
reconcile() scans all open Issues for divergences.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ExecRecord:
    """Minimal record of a phase execution state for use with project()."""
    phase: str   # "draft" | "develop" | "merge" | "sub"
    status: str  # "ready" | "running" | "done"


# Phase priority for most-advanced-wins selection
_PHASE_PRI: dict[str, int] = {"sub": 4, "merge": 3, "develop": 2, "draft": 1}
_STATUS_PRI: dict[str, int] = {"done": 3, "running": 2, "ready": 1}

_MANAGED_PHASES = frozenset({"draft", "develop", "merge", "sub"})
_MANAGED_STATUSES = frozenset({"ready", "running", "done"})
_MANAGED_ANDON_KINDS = frozenset({"decision", "blocked", "broken"})

# Attention priority: most urgent first
_ANDON_PRI: dict[str, int] = {"broken": 3, "decision": 2, "blocked": 1}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ns() -> str:
    from issuesmith.config import get_config
    return get_config().label_namespace


def _is_managed_label(label: str, ns: str) -> bool:
    """True if this label is owned by the phase/attention axes."""
    if not label.startswith(f"{ns}:"):
        return False
    suffix = label[len(ns) + 1:]
    if suffix in ("queued", "waiting"):
        return True
    for phase in _MANAGED_PHASES:
        for status in _MANAGED_STATUSES:
            if suffix == f"{phase}-{status}":
                return True
    for kind in _MANAGED_ANDON_KINDS:
        if suffix == f"andon-{kind}":
            return True
    return False


def _phase_label(ns: str, queue_state: str | None, exec_records: list[ExecRecord]) -> str | None:
    if queue_state == "queued":
        return f"{ns}:queued"
    if not exec_records:
        return None
    best = max(exec_records, key=lambda r: (_PHASE_PRI.get(r.phase, 0), _STATUS_PRI.get(r.status, 0)))
    return f"{ns}:{best.phase}-{best.status}"


def _attention_label(ns: str, andon_inbox: list) -> str | None:
    if not andon_inbox:
        return None
    best = max(andon_inbox, key=lambda a: _ANDON_PRI.get(a.kind, 0))
    return f"{ns}:andon-{best.kind}"


def _exec_records_from_labels(labels: set[str], ns: str) -> list[ExecRecord]:
    """Derive ExecRecord list from the current phase labels on an issue."""
    records = []
    for phase in _MANAGED_PHASES:
        for status in _MANAGED_STATUSES:
            if f"{ns}:{phase}-{status}" in labels:
                records.append(ExecRecord(phase=phase, status=status))
    return records


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def project(
    issue_number: int,
    *,
    queue_state: str | None,
    exec_records: list[ExecRecord],
    andon_inbox: list,
) -> set[str]:
    """Compute the desired label set for an issue (pure function, no side effects).

    Returns at most one phase-axis label and at most one attention-axis label.
    """
    ns = _ns()
    labels: set[str] = set()

    phase = _phase_label(ns, queue_state, exec_records)
    if phase:
        labels.add(phase)

    attn = _attention_label(ns, andon_inbox)
    if attn:
        labels.add(attn)

    return labels


def apply(client: Any, issue: dict[str, Any], desired: set[str]) -> None:
    """Apply label delta to bring issue to desired label set.

    Only touches managed labels (phase and attention axes).
    """
    ns = _ns()
    current = {
        lbl["name"] if isinstance(lbl, dict) else str(lbl)
        for lbl in issue.get("labels", [])
    }
    managed_current = {lbl for lbl in current if _is_managed_label(lbl, ns)}
    managed_desired = {lbl for lbl in desired if _is_managed_label(lbl, ns)}

    to_add = sorted(managed_desired - managed_current)
    to_remove = sorted(managed_current - managed_desired)

    if to_add or to_remove:
        client.issue_update(issue["number"], labels_add=to_add, labels_remove=to_remove)


def reconcile(
    client: Any,
    *,
    fix: bool = False,
    as_json: bool = False,
) -> list[dict[str, Any]]:
    """Scan all open Issues and report (or fix) managed-label divergences."""
    from issuesmith.andon import from_comment
    from issuesmith.queue_store import QueueStore

    ns = _ns()
    store = QueueStore()
    snap = store.snapshot()

    in_flight_issues = {
        entry["issue"]
        for entry in snap.in_flight
        if isinstance(entry.get("issue"), int)
    }
    queued_issues: set[int] = set()
    for rid in snap.active_order:
        req = snap.requests.get(rid)
        if req is not None and req.issue not in in_flight_issues:
            queued_issues.add(req.issue)

    # Collect open andons per issue
    andon_by_issue: dict[int, list] = {}
    for kind in ("decision", "blocked", "broken"):
        label = f"{ns}:andon-{kind}"
        try:
            andon_issues = client.list_issues(label=label, state="open")
        except Exception:
            continue
        for ai in (andon_issues or []):
            if not isinstance(ai, dict):
                continue
            num = ai.get("number")
            if not isinstance(num, int):
                continue
            try:
                comments = client.get_issue_comments(num)
            except Exception:
                comments = []
            for comment in comments or []:
                parsed = from_comment(comment.get("body", ""))
                if parsed is not None:
                    andon_by_issue.setdefault(num, []).append(parsed)

    # Fetch all open issues
    try:
        raw = client.api_request("issues?state=open&per_page=100", paginate=True)
    except Exception:
        try:
            raw = client.api_request("issues?state=open&per_page=100")
        except Exception:
            raw = []

    open_issues = [
        i for i in (raw or [])
        if isinstance(i, dict) and i.get("pull_request") is None
    ]

    divergences: list[dict[str, Any]] = []
    for issue in open_issues:
        num = issue.get("number")
        if not isinstance(num, int):
            continue

        current_labels = {
            lbl["name"] if isinstance(lbl, dict) else str(lbl)
            for lbl in issue.get("labels", [])
        }

        if num in queued_issues:
            qs: str | None = "queued"
            exec_recs: list[ExecRecord] = []
        else:
            qs = None
            exec_recs = _exec_records_from_labels(current_labels, ns)

        andons = andon_by_issue.get(num, [])
        desired = project(num, queue_state=qs, exec_records=exec_recs, andon_inbox=andons)

        managed_current = {lbl for lbl in current_labels if _is_managed_label(lbl, ns)}
        to_add = sorted(desired - managed_current)
        to_remove = sorted(managed_current - desired)

        if to_add or to_remove:
            div: dict[str, Any] = {"issue": num, "add": to_add, "remove": to_remove}
            divergences.append(div)
            if fix:
                apply(client, issue, desired)

    if as_json:
        print(json.dumps(divergences, ensure_ascii=False))
    else:
        for div in divergences:
            parts = [f"#{div['issue']}"]
            if div["add"]:
                parts.append("add: " + ", ".join(div["add"]))
            if div["remove"]:
                parts.append("remove: " + ", ".join(div["remove"]))
            print("  ".join(parts))

    return divergences
