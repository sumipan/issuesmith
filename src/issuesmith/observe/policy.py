"""issuesmith.observe.policy — evaluate events into actions, execute actions."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    ChainHaltedEvent,
    DagTerminatedEvent,
    DispatchBlockedEvent,
    ForgeUnavailableEvent,
    IssueStallEvent,
    LabelDriftEvent,
    ObserveEvent,
    OrphanExecEvent,
    SystemicStepFailureEvent,
    TaskTimeoutEvent,
    VersionSkewEvent,
)

if TYPE_CHECKING:
    from issuesmith.andon import AndonSink
    from issuesmith.config import ObserveConfig
    from issuesmith.queue_store import QueueStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WaitAction:
    reason: str = ""


@dataclass(frozen=True)
class AndonAction:
    kind: str
    issue: int = 0
    summary: str = ""
    evidence: str = ""
    # Stable identity of the condition (independent of counters in ``summary``), used to
    # raise the andon once per occurrence. Empty means "one andon per issue".
    key: str = ""

    @property
    def andon_id(self) -> str:
        return f"observe:{self.issue}:{self.key or 'observe'}:0"


@dataclass(frozen=True)
class HaltAction:
    scope: str = "all"
    reason: str = ""
    event_kind: str = ""


@dataclass(frozen=True)
class ResumeAction:
    reason: str = ""


@dataclass(frozen=True)
class ReleaseInFlightAction:
    issue: int
    phase: str = ""
    reason: str = ""


Action = WaitAction | AndonAction | HaltAction | ResumeAction | ReleaseInFlightAction


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    events: list[ObserveEvent],
    config: "ObserveConfig",
) -> list[Action]:
    actions: list[Action] = []
    for event in events:
        actions.extend(_evaluate_one(event, config))
    return actions


def _evaluate_one(event: ObserveEvent, config: "ObserveConfig") -> list[Action]:
    if isinstance(event, DagTerminatedEvent):
        reason = f"DAG {event.key} terminated at step {event.failed_step}"
        return [
            ReleaseInFlightAction(issue=event.issue, phase=event.phase, reason=reason),
            AndonAction(
                kind="blocked",
                issue=event.issue,
                summary=f"{reason}; in_flight released",
                evidence=f"uuid={event.failed_uuid} result={event.result_path}",
                key=f"dag_terminated:{_strip_generation(event.key)}",
            ),
        ]

    if isinstance(event, IssueStallEvent):
        return [
            AndonAction(
                kind="blocked",
                issue=event.issue,
                summary=f"issue #{event.issue} stalled in {event.phase} for {event.minutes} minutes",
                key=f"stall:{event.phase}",
            )
        ]

    if isinstance(event, OrphanExecEvent):
        issue = event.issue or 0
        return [
            AndonAction(
                kind="blocked",
                issue=issue,
                summary=f"orphan exec UUID {event.uuid} has no in_flight tracking",
                key=f"orphan:{event.uuid}",
            )
        ]

    if isinstance(event, LabelDriftEvent):
        return [WaitAction(reason=f"label drift on issue #{event.issue}: add={event.add} remove={event.remove}")]

    if isinstance(event, ChainHaltedEvent):
        return [
            AndonAction(
                kind="blocked",
                issue=event.parent,
                summary=f"milestone chain parent #{event.parent} is halted: {event.reason}",
                key="chain_halted",
            )
        ]

    if isinstance(event, TaskTimeoutEvent):
        return [
            AndonAction(
                kind="blocked",
                issue=0,
                summary=f"task {event.uuid} timed out after {event.elapsed} minutes",
                key=f"timeout:{event.uuid}",
            )
        ]

    if isinstance(event, DispatchBlockedEvent):
        return [WaitAction(reason=f"dispatch blocked for {event.request_id}: {event.reason}")]

    if isinstance(event, SystemicStepFailureEvent):
        scope = _resolve_phase_scope(event.step, config)
        return [
            HaltAction(
                scope=scope,
                reason=(
                    f"systemic failure at step {event.step}: "
                    f"{event.failure_class} across {len(event.issues)} issues"
                ),
                event_kind="systemic_step_failure",
            ),
            AndonAction(
                kind="broken",
                issue=event.issues[0] if event.issues else 0,
                summary=f"systemic step failure: {event.step} {event.failure_class}",
                evidence=f"affected issues: {list(event.issues)}",
                key=f"systemic:{event.step}:{event.failure_class}",
            ),
        ]

    if isinstance(event, ForgeUnavailableEvent):
        return [
            HaltAction(
                scope="all",
                reason=f"forge unavailable: {event.consecutive} consecutive errors",
                event_kind="forge_unavailable",
            ),
            AndonAction(
                kind="broken",
                issue=0,
                summary=f"forge API unavailable: {event.consecutive} consecutive errors",
                key="forge_unavailable",
            ),
        ]

    if isinstance(event, AllEnginesPausedEvent):
        return [WaitAction(reason=f"all engines paused: {list(event.roles)}")]

    if isinstance(event, VersionSkewEvent):
        return [
            AndonAction(
                kind="decision",
                issue=0,
                summary=f"version skew: {event.package} pinned={event.pinned} installed={event.installed}",
                key=f"skew:{event.package}",
            )
        ]

    return [WaitAction(reason=f"unknown event: {event.kind}")]


def _strip_generation(key: str) -> str:
    """Remove trailing :N generation suffix from an idempotency key when present.

    Production keys follow the pattern ``{workflow}:{phase}:{issue}:{gen}`` where
    ``gen`` is a non-negative integer.  Stripping it yields a stable,
    generation-independent dedup key (e.g. ``issuesmith:impl:3628:3`` →
    ``issuesmith:impl:3628``).  Keys without a digit-only last segment are
    returned unchanged.
    """
    parts = key.split(":")
    if len(parts) >= 4 and parts[-1].isdigit():
        return ":".join(parts[:-1])
    return key


def _label_namespace() -> str:
    from issuesmith.config import get_config as _get_config
    return _get_config().label_namespace


def _resolve_phase_scope(step: str, config: "ObserveConfig") -> str:
    from issuesmith.config import get_config as _get_config

    cfg = _get_config()
    for phase in cfg.phases:
        if phase.entry_step == step:
            return f"phase:{phase.name}"
    return "all"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute(
    actions: list[Action],
    store: "QueueStore",
    sinks: list["AndonSink"],
    *,
    client: Any | None = None,
) -> None:
    """Apply policy actions.

    Andons are raised **once per occurrence**: ``store`` remembers the ids of the andons whose
    condition is still present, and only ids that are new in this call are raised. When the
    condition disappears the id is forgotten, so a later recurrence is reported once more.
    With ``client`` the andon is canonical (Issue comment + attention label via
    ``raise_andon``, sinks included) for actions bound to an Issue; without ``client`` or for
    ``issue == 0`` it is only emitted to ``sinks``. (sumipan/nexus#3621)
    """
    from issuesmith.andon import Andon, answer_if_open, raise_andon

    andon_actions = [a for a in actions if isinstance(a, AndonAction)]
    new_ids, resolved_ids = store.sync_observe_andons({a.andon_id for a in andon_actions})

    if resolved_ids and client is not None:
        # dag_terminated andons are only auto-resolved when the issue is back in_flight
        # (the first tick that processes a failure removes in_flight + running label, so
        # subsequent ticks can't detect the failure anymore — we must wait for re-dispatch).
        # Deferred ids are retained in the store so they are re-checked on later ticks.
        deferred: set[str] = set()
        snap = store.snapshot()
        in_flight_issues: set[int] = {
            entry.get("issue")  # type: ignore[misc]
            for entry in snap.in_flight
            if isinstance(entry, dict) and isinstance(entry.get("issue"), int)
        }
        for resolved_id in resolved_ids:
            if ":dag_terminated:" in resolved_id:
                parts = resolved_id.split(":")
                try:
                    issue_num: int | None = int(parts[1]) if len(parts) >= 2 else None
                except (ValueError, IndexError):
                    issue_num = None
                if issue_num is None or issue_num not in in_flight_issues:
                    logger.debug(
                        "defer auto-resolve: dag_terminated %s, issue not in_flight", resolved_id
                    )
                    deferred.add(resolved_id)
                    continue
            try:
                answer_if_open(client, resolved_id, "auto-resolved: condition cleared")
            except Exception:
                logger.exception("answer_if_open failed for %s", resolved_id)
        store.retain_observe_andons(deferred)

    for action in actions:
        if isinstance(action, HaltAction):
            store.set_halt(True, action.reason, scope=action.scope, event=action.event_kind)
            logger.info("halt set: scope=%s reason=%s", action.scope, action.reason)

        elif isinstance(action, ResumeAction):
            store.clear_halt()
            logger.info("halt cleared: %s", action.reason)

        elif isinstance(action, AndonAction):
            if action.andon_id not in new_ids:
                logger.debug("andon already raised: %s", action.andon_id)
                continue
            andon = Andon(
                id=action.andon_id,
                kind=action.kind,
                issue=action.issue,
                step="observe",
                summary=action.summary,
                evidence=action.evidence,
            )
            if client is not None and action.issue:
                try:
                    raise_andon(client, andon, sinks=sinks)
                except Exception:
                    logger.exception("raise_andon failed for %s", action.andon_id)
                continue
            for sink in sinks:
                try:
                    sink.emit(andon)
                except Exception:
                    logger.exception("andon sink emit failed")

        elif isinstance(action, ReleaseInFlightAction):
            store.remove_in_flight(action.issue)
            logger.info("in_flight released: issue=#%s reason=%s", action.issue, action.reason)
            if client is not None and action.phase:
                ns = _label_namespace()
                try:
                    client.issue_update(action.issue, labels_remove=[f"{ns}:{action.phase}-running"])
                except Exception:
                    logger.exception(
                        "issue_update failed removing running label for #%s", action.issue
                    )

        elif isinstance(action, WaitAction):
            logger.debug("wait: %s", action.reason)
