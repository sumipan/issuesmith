"""labels.py — phase/attention label projection and reconciliation (#3484 / #4807).

The state -> label rules live in :mod:`issuesmith.projection` (pure functions).
:func:`project_issue` is the only writer that applies a state change to an Issue.
``ExecRecord`` / :func:`project` / :func:`apply` / :func:`run_hygiene` keep their
signatures for existing callers and delegate to the projection.
reconcile() scans all open Issues for divergences.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Callable, Iterable

from issuesmith import projection
from issuesmith.contract import ANDON_KINDS, ANDON_PREFIX, PHASE_STATUSES
from issuesmith.projection import IssueState

if TYPE_CHECKING:
    from issuesmith.config import IssuesmithConfig

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ExecRecord:
    """Minimal record of a phase execution state for use with project()."""
    phase: str   # a config.phases name
    status: str  # a PHASE_STATUSES item


def _get_managed_phases() -> frozenset[str]:
    from issuesmith.config import get_config
    return frozenset(p.name for p in get_config().phases)


# Phase names of the loaded config, kept for callers that read it (config-driven, #3484).
_MANAGED_PHASES: frozenset[str] = _get_managed_phases()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _config(ns: str | None = None) -> IssuesmithConfig:
    from issuesmith.config import get_config
    cfg = get_config()
    if ns is not None and ns != cfg.label_namespace:
        cfg = replace(cfg, label_namespace=ns)
    return cfg


def _ns() -> str:
    return _config().label_namespace


def _label_names(raw: Any) -> list[str]:
    return [
        lbl["name"] if isinstance(lbl, dict) else str(lbl)
        for lbl in (raw if isinstance(raw, list) else [])
    ]


def _is_managed_label(label: str, ns: str) -> bool:
    """True if this label is owned by the phase/attention axes."""
    return projection.is_managed(label, _config(ns))


def _exec_records_from_labels(labels: set[str], ns: str) -> list[ExecRecord]:
    """Derive ExecRecord list from the current phase labels on an issue."""
    return [
        ExecRecord(phase=phase.name, status=status)
        for phase in _config(ns).phases
        for status in PHASE_STATUSES
        if f"{ns}:{phase.name}-{status}" in labels
    ]


def _state(
    exec_records: Iterable[ExecRecord], andon_kinds: Iterable[str], *, queued: bool
) -> IssueState:
    """IssueState holding the most advanced status of each phase in ``exec_records``."""
    phases: dict[str, str] = {}
    for rec in exec_records:
        if rec.status not in PHASE_STATUSES:
            continue
        prev = phases.get(rec.phase)
        if prev is None or PHASE_STATUSES.index(rec.status) > PHASE_STATUSES.index(prev):
            phases[rec.phase] = rec.status
    return IssueState(phases=phases, andon_kinds=frozenset(andon_kinds), queued=queued)


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

    Compatibility wrapper over :func:`issuesmith.projection.project`: at most one
    phase-axis label, at most one attention-axis label, the additive ``queued`` marker
    while the issue has a pending queue request (sumipan/nexus#3601), and the
    precondition labels of active phases (#3626).
    """
    state = _state(
        exec_records,
        (getattr(a, "kind", "") for a in andon_inbox),
        queued=queue_state == "queued",
    )
    return set(projection.project(state, _config()))


def apply(client: Any, issue: dict[str, Any], desired: set[str]) -> None:
    """Apply label delta to bring issue to desired label set.

    Only touches managed labels (phase and attention axes).
    """
    current = _label_names(issue.get("labels", []))
    to_add, to_remove = projection.diff(current, frozenset(desired), _config())
    if to_add or to_remove:
        client.issue_update(issue["number"], labels_add=to_add, labels_remove=to_remove)


def project_issue(
    client: Any, issue_number: int, change: Callable[[IssueState], IssueState]
) -> tuple[list[str], list[str]]:
    """Apply a state change to an Issue's labels; the only label writer of the runner (#4807).

    Reads the current labels, computes ``state_from_labels`` -> ``change`` -> ``project``
    -> ``diff`` and sends the difference in a single ``issue_update`` (nothing when it is
    empty). Forge errors are reported on stderr and swallowed. Returns ``(add, remove)``.
    """
    cfg = _config()
    try:
        data = client.issue_get(issue_number, fields=["labels"])
        current = _label_names(data.get("labels") if isinstance(data, dict) else None)
        state = change(projection.state_from_labels(current, cfg))
        add, remove = projection.diff(current, projection.project(state, cfg), cfg)
        if add or remove:
            delta: dict[str, list[str]] = {}
            if add:
                delta["labels_add"] = add
            if remove:
                delta["labels_remove"] = remove
            client.issue_update(issue_number, **delta)
    except Exception as exc:  # noqa: BLE001 - label projection must not fail the caller
        print(f"WARNING: label projection failed for #{issue_number}: {exc}", file=sys.stderr)
        return [], []
    return add, remove


def reconcile(
    client: Any,
    *,
    fix: bool = False,
    as_json: bool = False,
) -> list[dict[str, Any]]:
    """Scan all open Issues and report (or fix) managed-label divergences."""
    from issuesmith.andon import from_comment
    from issuesmith.queue_store import QueueStore

    cfg = _config()
    ns = cfg.label_namespace
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
    for kind in ANDON_KINDS:
        label = f"{ns}:{ANDON_PREFIX}{kind}"
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

        current_labels = _label_names(issue.get("labels", []))
        # State expressed by the labels, with the queue's queued marker and the andon
        # kinds of the open andon comments laid over it (#4807).
        state = replace(
            projection.state_from_labels(current_labels, cfg),
            queued=num in queued_issues,
            andon_kinds=frozenset(a.kind for a in andon_by_issue.get(num, [])),
        )
        to_add, to_remove = projection.diff(
            current_labels, projection.project(state, cfg), cfg
        )

        if to_add or to_remove:
            div: dict[str, Any] = {"issue": num, "add": to_add, "remove": to_remove}
            divergences.append(div)
            if fix:
                client.issue_update(num, labels_add=to_add, labels_remove=to_remove)

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


def run_hygiene(
    issue_number: int,
    dry_run: bool = False,
    client: Any = None,
) -> tuple[int, dict]:
    """Remove stale phase labels from an issue (replaces ops/label_hygiene.py)."""
    from ghdag.exceptions import GitHubApiError
    from ghdag.forge import get_forge as _get_forge

    if client is None:
        client = _get_forge()
    try:
        data = client.issue_get(issue_number, fields=["labels"])
    except GitHubApiError as exc:
        if getattr(exc, "status_code", None) == 404:
            return 1, {"error": "issue not found"}
        return 3, {"error": str(exc)}

    ns = _ns()
    labels_set = {lbl["name"] for lbl in (data.get("labels") or [])}
    exec_recs = _exec_records_from_labels(labels_set, ns)
    desired = project(0, queue_state=None, exec_records=exec_recs, andon_inbox=[])
    managed_current = {lbl for lbl in labels_set if _is_managed_label(lbl, ns)}
    stale = sorted(managed_current - desired)

    if stale and not dry_run:
        try:
            client.issue_update(issue_number, labels_remove=stale)
        except GitHubApiError as exc:
            return 3, {"error": str(exc), "stale": stale}

    return 0, {"removed": [] if dry_run else stale, "stale": stale, "dry_run": dry_run}
