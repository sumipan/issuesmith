"""Deterministic gates and ordering for issuesmith queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

# parse_frontmatter_fields moved to contract.py (#4238). Re-export for existing callers.
# noqa F401 keeps ruff --fix from stripping the re-export (upstream #4259).
from issuesmith.contract import parse_frontmatter_fields, validate_frontmatter  # noqa: F401
from issuesmith.queue_store import (
    DEFAULT_SEED_PATH,
    PRIORITY_RANK,
    QueueRequest,
)

DecisionKind = Literal[
    "keep",
    "closed",
    "already_processed",
    "rejected",
    "duplicate",
    "repair",
]


def _build_phase_labels(status: str) -> dict[str, str]:
    from issuesmith.config import get_config

    cfg = get_config()
    return {p.name: f"{cfg.label_namespace}:{p.name}-{status}" for p in cfg.phases}


class _LazyPhaseLabels:
    """Phase label map rebuilt from the current config on each access (#4790)."""

    __slots__ = ("_status",)

    def __init__(self, status: str) -> None:
        self._status = status

    def _dict(self) -> dict[str, str]:
        return _build_phase_labels(self._status)

    def _label_for(self, phase: str) -> str:
        labels = self._dict()
        if phase in labels:
            return labels[phase]
        from issuesmith.config import get_config

        return f"{get_config().label_namespace}:{phase}-{self._status}"

    def __getitem__(self, key: str) -> str:
        return self._label_for(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._dict().get(key, default)

    def items(self):
        return self._dict().items()

    def values(self):
        return self._dict().values()

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return self._dict() == other
        return NotImplemented

    def __repr__(self) -> str:
        return repr(self._dict())


READY_LABEL: _LazyPhaseLabels = _LazyPhaseLabels("ready")
RUNNING_LABEL: _LazyPhaseLabels = _LazyPhaseLabels("running")
DONE_LABEL: _LazyPhaseLabels = _LazyPhaseLabels("done")


def _msg(key: str, /, **kwargs: Any) -> str:
    """Render a ``queue_triage.*`` language-pack message (posted as an Issue comment)."""
    from issuesmith.config import get_config

    return get_config().language.message(f"queue_triage.{key}", **kwargs)


def get_terminal_without_merge() -> frozenset[str]:
    """Terminal labels for closed-without-merge, plus config ``terminal_labels`` (#4790)."""
    from issuesmith.config import get_config

    cfg = get_config()
    ns = cfg.label_namespace
    result: set[str] = set()
    for label in cfg.terminal_without_merge:
        result.add(label if ":" in label else f"{ns}:{label}")
    closing = cfg.closing_phase()
    # Match by suffix so cross-namespace done labels (e.g. ``wsl:develop-done``) count
    # as closed-with-merge too (#7811).
    closing_suffix = f":{closing.name}-done" if closing is not None else None
    for label in cfg.terminal_labels:
        if closing_suffix is None or not label.endswith(closing_suffix):
            result.add(label)
    return frozenset(result)


COMMENT_MARKER = "<!-- ISSUESMITH_QUEUE_REQUEST:{request_id}:{outcome} -->"

missing_yaml_fields = validate_frontmatter


@dataclass
class Decision:
    kind: DecisionKind
    reason: str
    close_issue: bool = False
    comment: bool = True
    add_rejected_label: bool = False


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
    force: bool = False,
) -> Decision:
    state = str(issue.get("state", "")).upper()
    labels = label_names(issue)
    phase = request.phase
    done = DONE_LABEL[phase]

    if state == "CLOSED":
        # force=True and the phase is not done -> go on to keep (merge recovery after PR auto-close).
        if force and done not in labels:
            pass
        else:
            return Decision(kind="closed", reason=_msg("closed", issue=request.issue), comment=True)

    ready = READY_LABEL[phase]
    running = RUNNING_LABEL[phase]
    if ready in labels:
        return Decision(
            kind="already_processed",
            reason=_msg("already_present", label=ready),
            comment=False,
        )
    if running in labels:
        return Decision(
            kind="already_processed",
            reason=_msg("already_present", label=running),
            comment=True,
        )
    if done in labels and not force:
        return Decision(
            kind="already_processed",
            reason=_msg("already_present", label=done),
            comment=True,
        )

    from issuesmith.config import get_config

    cfg = get_config()
    try:
        ph = cfg.phase(phase)
    except KeyError:
        ph = None
    if ph is not None:
        for excl in cfg.excluded_labels(ph.name):
            if excl in labels:
                return Decision(
                    kind="rejected",
                    reason=_msg("phase_excludes_label", phase=ph.name, label=excl),
                    comment=True,
                    add_rejected_label=True,
                )
        # Labels in our own namespace (e.g. a prior phase's done label) are waited on
        # by phase_preconditions; only missing foreign labels are rejected here.
        own_prefix = f"{cfg.label_namespace}:"
        for req in cfg.required_labels(ph.name):
            if not req.startswith(own_prefix) and req not in labels:
                return Decision(
                    kind="rejected",
                    reason=_msg("phase_requires_label", phase=ph.name, label=req),
                    comment=True,
                    add_rejected_label=True,
                )

    target_repo = parse_frontmatter_fields(str(issue.get("body") or "")).get("target_repo")
    if isinstance(target_repo, str) and target_repo.strip():
        if target_repo not in cfg.supported_repos:
            supported = ", ".join(sorted(cfg.supported_repos)) or "(none)"
            return Decision(
                kind="rejected",
                reason=_msg(
                    "unsupported_repo",
                    repo=target_repo,
                    namespace=cfg.label_namespace,
                    supported=supported,
                ),
                comment=True,
                add_rejected_label=True,
            )

    # force=True: allow redispatch even when the done label is present.
    # This is the path for re-enqueueing, via the suggested `enqueue --force`, an Issue
    # whose phase was sent back to *-done by a CP2 FAIL etc. (regression found 2026-09-04).
    # ready (already enqueued) is not bypassed even with force, since that would
    # contradict the double-enqueue race fix of #2813.

    design = get_config().design_phase()
    if design is not None and phase != design.name:
        _, _, design_done = get_config().phase_labels(design.name)
        if design_done in labels:
            missing = validate_frontmatter(str(issue.get("body") or ""))
            if missing:
                return Decision(
                    kind="repair",
                    reason=_msg("missing_yaml", fields=", ".join(missing)),
                    comment=True,
                    add_rejected_label=False,
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
    """Re-apply hard constraints: high before others; wait>=7d first within priority.

    Within the same (priority, aging) bucket the input relative order is preserved.
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

    # Stable relative order within the same constraint key via original index.
    index = {rid: i for i, rid in enumerate(order)}

    def full_key(req: QueueRequest) -> tuple[int, int, int]:
        wait = wait_seconds(req)
        aged = 0 if wait >= 7 * 24 * 3600 else 1
        return (PRIORITY_RANK.get(req.priority, 99), aged, index.get(req.request_id, 0))

    return [r.request_id for r in sorted(reqs, key=full_key)]


def apply_priority_bucket_order(
    llm_order: list[str],
    active: list[QueueRequest],
) -> list[str]:
    """Restrict reorder to within priority buckets (high → normal → low → other).

    Relative order inside each priority bucket is preserved. Requests missing from
    ``llm_order`` but present in ``active`` are appended at the end of their bucket
    in the original active order.
    """
    req_map = {r.request_id: r for r in active}
    seen: set[str] = set()
    buckets: dict[int, list[str]] = {}
    for rid in llm_order:
        req = req_map.get(rid)
        if req is None or rid in seen:
            continue
        rank = PRIORITY_RANK.get(req.priority, 99)
        buckets.setdefault(rank, []).append(rid)
        seen.add(rid)
    for req in active:
        if req.request_id in seen:
            continue
        rank = PRIORITY_RANK.get(req.priority, 99)
        buckets.setdefault(rank, []).append(req.request_id)
        seen.add(req.request_id)
    result: list[str] = []
    for rank in sorted(buckets.keys()):
        result.extend(buckets[rank])
    return result


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
        from issuesmith.config import get_config

        design = get_config().design_phase()
        fallback = (
            design.name
            if design is not None
            else (get_config().phases[0].name if get_config().phases else "")
        )
        phase = next(
            (pname for pname, plabel in READY_LABEL.items() if label == plabel),
            fallback,
        )
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
    raw_window = data.get("window")
    window: dict[str, Any] = raw_window if isinstance(raw_window, dict) else {}
    start = str(window.get("start", start))
    end = str(window.get("end", end))
    idle = int(data.get("idle_minutes", idle))
    return start, end, idle


def seed_extra_sources(path: Path | None = None) -> frozenset[str]:
    """Queue ``source`` values that dispatch only inside the seed night window."""
    seed_path = path or DEFAULT_SEED_PATH
    if not seed_path.exists():
        return frozenset()
    data = yaml.safe_load(seed_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return frozenset()
    raw_window = data.get("window")
    window: dict[str, Any] = raw_window if isinstance(raw_window, dict) else {}
    raw_extra = window.get("extra_sources")
    if not isinstance(raw_extra, list):
        return frozenset()
    return frozenset(
        str(item) for item in raw_extra if isinstance(item, str) and item.strip()
    )
