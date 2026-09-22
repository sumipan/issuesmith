"""issuesmith.observe.policy — evaluate events into actions, execute actions."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from issuesmith.observe.events import (
    AllEnginesPausedEvent,
    ChainHaltedEvent,
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


@dataclass(frozen=True)
class HaltAction:
    scope: str = "all"
    reason: str = ""
    event_kind: str = ""


@dataclass(frozen=True)
class ResumeAction:
    reason: str = ""


Action = WaitAction | AndonAction | HaltAction | ResumeAction


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
    if isinstance(event, IssueStallEvent):
        return [
            AndonAction(
                kind="blocked",
                issue=event.issue,
                summary=f"issue #{event.issue} stalled in {event.phase} for {event.minutes} minutes",
            )
        ]

    if isinstance(event, OrphanExecEvent):
        issue = event.issue or 0
        return [
            AndonAction(
                kind="blocked",
                issue=issue,
                summary=f"orphan exec UUID {event.uuid} has no in_flight tracking",
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
            )
        ]

    if isinstance(event, TaskTimeoutEvent):
        return [
            AndonAction(
                kind="blocked",
                issue=0,
                summary=f"task {event.uuid} timed out after {event.elapsed} minutes",
            )
        ]

    if isinstance(event, DispatchBlockedEvent):
        return [WaitAction(reason=f"dispatch blocked for {event.request_id}: {event.reason}")]

    if isinstance(event, SystemicStepFailureEvent):
        scope = _resolve_phase_scope(event.step, config)
        return [
            HaltAction(
                scope=scope,
                reason=f"systemic failure at step {event.step}: {event.failure_class} across {len(event.issues)} issues",
                event_kind="systemic_step_failure",
            ),
            AndonAction(
                kind="broken",
                issue=event.issues[0] if event.issues else 0,
                summary=f"systemic step failure: {event.step} {event.failure_class}",
                evidence=f"affected issues: {list(event.issues)}",
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
            )
        ]

    return [WaitAction(reason=f"unknown event: {event.kind}")]


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
) -> None:
    from issuesmith.andon import Andon

    for action in actions:
        if isinstance(action, HaltAction):
            store.set_halt(True, action.reason, scope=action.scope, event=action.event_kind)
            logger.info("halt set: scope=%s reason=%s", action.scope, action.reason)

        elif isinstance(action, ResumeAction):
            store.clear_halt()
            logger.info("halt cleared: %s", action.reason)

        elif isinstance(action, AndonAction):
            andon = Andon(
                id=f"observe::{action.issue}::observe::0",
                kind=action.kind,
                issue=action.issue,
                step="observe",
                summary=action.summary,
                evidence=action.evidence,
            )
            for sink in sinks:
                try:
                    sink.emit(andon)
                except Exception:
                    logger.exception("andon sink emit failed")

        elif isinstance(action, WaitAction):
            logger.debug("wait: %s", action.reason)
