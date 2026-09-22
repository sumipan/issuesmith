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
