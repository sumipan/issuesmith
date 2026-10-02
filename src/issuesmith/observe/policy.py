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
    GitHubApiLowEvent,
    GitHubApiRecoveredEvent,
    IssueStallEvent,
    LabelDriftEvent,
    MainGreenEvent,
    MainRedEvent,
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
    # True: do not overwrite an existing halt raised by a different event (e.g. a manual or
    # systemic_step_failure halt must not be narrowed to this action's scope).
    keep_existing: bool = False


@dataclass(frozen=True)
class ResumeAction:
    reason: str = ""
    # Non-empty: clear the halt only when it was raised by this event kind.
    event_kind: str = ""


@dataclass(frozen=True)
class ReleaseInFlightAction:
    issue: int
    phase: str = ""
    reason: str = ""


@dataclass(frozen=True)
class RemoveRunningLabelAction:
    """Remove the -running label without releasing in_flight (nexus #4137 Fix 1).

    Used by DagTerminatedEvent so that the allow_paths lock is preserved until
    the DAG recovers (dag recover) or is explicitly reset/abandoned.
    """

    issue: int
    phase: str = ""


Action = (
    WaitAction
    | AndonAction
    | HaltAction
    | ResumeAction
    | ReleaseInFlightAction
    | RemoveRunningLabelAction
)


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
            RemoveRunningLabelAction(issue=event.issue, phase=event.phase),
            AndonAction(
                kind="blocked",
                issue=event.issue,
                summary=f"{reason}; running label removed",
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

    # GitHub API brake (#3769): notify only; dispatch_one already holds new work back.
    if isinstance(event, GitHubApiLowEvent):
        return [
            AndonAction(
                kind="blocked",
                issue=0,
                summary=f"GitHub API rate limit low: {event.remaining} remaining",
                key="github_api_low",
            )
        ]

    if isinstance(event, GitHubApiRecoveredEvent):
        return [
            AndonAction(
                kind="recovered",
                issue=0,
                summary="GitHub API rate limit recovered",
                key="github_api_recovered",
            )
        ]

    if isinstance(event, MainRedEvent):
        sha12 = event.sha[:12]
        return [
            HaltAction(
                scope="phase:develop",
                reason=f"main red at {sha12}: {event.reason}",
                event_kind="main_red",
                keep_existing=True,
            ),
            AndonAction(
                kind="broken",
                issue=0,
                summary=f"main is red at {sha12}: {event.reason}",
                evidence=f"failing: {', '.join(event.failing)}",
                key="main_red",
            ),
        ]

    if isinstance(event, MainGreenEvent):
        return [ResumeAction(reason=f"main green at {event.sha[:12]}", event_kind="main_red")]

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


def _issue_has_open_linked_pr(
    client: Any,
    issue_number: int,
    *,
    target_repo: str | None = None,
) -> bool:
    """Return True when *issue_number* has an open PR linked on the forge.

    Matches PRs whose head ref is ``issue-{N}`` / ``issue-{N}-*`` or whose
    title/body mention ``#{N}`` (covers ``Closes #N`` / ``Refs #N`` and
    cross-repo ``owner/repo#N``). Lists open PRs of *target_repo* with one raw
    API call (``head.ref`` and ``body`` included); falls back to
    ``client.pr_list`` (normalized: ``headRefName``, no body) when the repo is
    unknown or the raw API is unavailable. No per-PR fetch.
    """
    import re

    from issuesmith.forge_api import api_request

    issue_ref = re.compile(rf"(?<!\d)#{issue_number}(?!\d)")
    branch_prefix = f"issue-{issue_number}-"

    def _matches(pr: dict[str, Any]) -> bool:
        head = pr.get("head")
        ref = head.get("ref") if isinstance(head, dict) else pr.get("headRefName")
        if isinstance(ref, str) and (
            ref.startswith(branch_prefix) or ref == f"issue-{issue_number}"
        ):
            return True
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        return bool(issue_ref.search(title) or issue_ref.search(body))

    prs: Any = None
    if target_repo:
        try:
            prs = api_request(client, f"repos/{target_repo}/pulls?state=open&per_page=100")
        except Exception:
            prs = None
    if not isinstance(prs, list):
        try:
            prs = client.pr_list(state="open", limit=100)
        except Exception:
            prs = []
    if not isinstance(prs, list):
        return False
    return any(isinstance(pr, dict) and _matches(pr) for pr in prs)


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


def _in_flight_target_repo(store: "QueueStore", issue: int) -> str | None:
    for entry in store.snapshot().in_flight:
        if entry.get("issue") == issue:
            repo = entry.get("target_repo")
            if isinstance(repo, str) and repo.strip():
                return repo
    return None


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
        # dag_terminated andons are auto-resolved when the event clears (DAG recovered) AND
        # the issue is still in_flight. While the DAG is still failed, _detect_dag_terminated
        # keeps firing, so the andon stays in the active set and never reaches resolved_ids.
        # When the DAG recovers (new run started via dag recover), the event stops and the
        # andon moves to resolved_ids; since in_flight is retained (Fix 1, nexus #4137),
        # auto-resolve fires immediately. If in_flight was released by reset/abandon,
        # the id is deferred and retained until in_flight is restored (or the andon is
        # manually answered). Deferred ids are retained in the store for re-checking.
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
            if action.keep_existing:
                current = store.snapshot()
                if current.halt and current.halt_event != action.event_kind:
                    logger.info(
                        "halt kept: existing event=%s, skipping %s",
                        current.halt_event, action.event_kind,
                    )
                    continue
            store.set_halt(True, action.reason, scope=action.scope, event=action.event_kind)
            logger.info("halt set: scope=%s reason=%s", action.scope, action.reason)

        elif isinstance(action, ResumeAction):
            if action.event_kind and store.snapshot().halt_event != action.event_kind:
                logger.info("halt not cleared: not raised by %s", action.event_kind)
                continue
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
            if client is not None and _issue_has_open_linked_pr(
                client,
                action.issue,
                target_repo=_in_flight_target_repo(store, action.issue),
            ):
                logger.info(
                    "in_flight release skipped: issue=#%s has open linked PR",
                    action.issue,
                )
                continue
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

        elif isinstance(action, RemoveRunningLabelAction):
            logger.info(
                "running label removed (in_flight retained): issue=#%s", action.issue
            )
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
