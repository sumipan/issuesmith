"""issuesmith.observe.events — typed observe event definitions."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ObserveEvent:
    kind: str


@dataclass(frozen=True)
class IssueStallEvent(ObserveEvent):
    kind: str = "issue_stall"
    issue: int = 0
    phase: str = ""
    minutes: int = 0


@dataclass(frozen=True)
class OrphanExecEvent(ObserveEvent):
    kind: str = "orphan_exec"
    uuid: str = ""
    issue: int | None = None


@dataclass(frozen=True)
class LabelDriftEvent(ObserveEvent):
    kind: str = "label_drift"
    issue: int = 0
    # Sorted tuples (not sets) so the event is JSON-serializable and ordered (#3590).
    add: tuple[str, ...] = ()
    remove: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainHaltedEvent(ObserveEvent):
    kind: str = "chain_halted"
    parent: int = 0
    reason: str = ""


@dataclass(frozen=True)
class TaskTimeoutEvent(ObserveEvent):
    kind: str = "task_timeout"
    uuid: str = ""
    elapsed: int = 0


@dataclass(frozen=True)
class DispatchBlockedEvent(ObserveEvent):
    kind: str = "dispatch_blocked"
    request_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SystemicStepFailureEvent(ObserveEvent):
    kind: str = "systemic_step_failure"
    step: str = ""
    failure_class: str = ""
    issues: tuple[int, ...] = ()


@dataclass(frozen=True)
class ForgeUnavailableEvent(ObserveEvent):
    kind: str = "forge_unavailable"
    consecutive: int = 0


@dataclass(frozen=True)
class AllEnginesPausedEvent(ObserveEvent):
    kind: str = "all_engines_paused"
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class VersionSkewEvent(ObserveEvent):
    kind: str = "version_skew"
    package: str = ""
    pinned: str = ""
    installed: str = ""


@dataclass(frozen=True)
class DagTerminatedEvent(ObserveEvent):
    kind: str = "dag_terminated"
    issue: int = 0
    key: str = ""
    phase: str = ""
    failed_step: str = ""
    failed_uuid: str = ""
    result_path: str = ""
    namespace: str = ""
    result_first_line: str = ""
    failed_at: str = ""
    failure_class: str = ""
    stderr_tail: str = ""
    issue_state: str = ""
    state_reason: str | None = None
    closed_at: str | None = None
    # Suppress andon (pipeline andon in time window); still mark seen when True.
    suppress_andon: bool = False
    # Closed with not_planned: no comment, reopen, or andon; mark seen only.
    not_planned: bool = False
    # Same issue:step already reopened once; comment only, no reopen/andon.
    reopen_guard: bool = False
    # Issue is closed and should be reopened before andon.
    needs_reopen: bool = False
    # Add failed_uuid to observe_failure_seen after actions apply.
    mark_seen: bool = False
    # Repeat tick for an already-seen failure (andon sync only).
    sync_only: bool = False


@dataclass(frozen=True)
class ObserveFailureWatchSeedEvent(ObserveEvent):
    kind: str = "observe_failure_watch_seed"
    watch_since: str = ""
    failed_uuids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObserveLedgerPatchEvent(ObserveEvent):
    kind: str = "observe_ledger_patch"
    watch_since: str | None = None
    seen_add: tuple[str, ...] = ()
    seen_remove: tuple[str, ...] = ()
    reopened_add: tuple[str, ...] = ()
    unstarted_set: tuple[tuple[str, str], ...] = ()
    unstarted_remove: tuple[str, ...] = ()


@dataclass(frozen=True)
class UnstartedTaskEvent(ObserveEvent):
    kind: str = "unstarted_task"
    issue: int = 0
    key: str = ""
    step: str = ""
    uuid: str = ""
    first_seen: str = ""
    timeout_at: str = ""
    namespace: str = ""
    issue_state: str = ""
    state_reason: str | None = None
    closed_at: str | None = None
    not_planned: bool = False
    reopen_guard: bool = False
    needs_reopen: bool = False
    mark_seen: bool = False


@dataclass(frozen=True)
class GitHubApiLowEvent(ObserveEvent):
    kind: str = "github_api_low"
    remaining: int = 0


@dataclass(frozen=True)
class GitHubApiRecoveredEvent(ObserveEvent):
    kind: str = "github_api_recovered"


@dataclass(frozen=True)
class MainRedEvent(ObserveEvent):
    kind: str = "main_red"
    sha: str = ""
    reason: str = ""
    failing: tuple[str, ...] = ()


@dataclass(frozen=True)
class MainGreenEvent(ObserveEvent):
    kind: str = "main_green"
    sha: str = ""
