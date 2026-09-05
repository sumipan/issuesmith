"""issuesmith queue store — JSONL requests, state, and exclusive lock."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Literal

from issuesmith.config import get_config

Phase = Literal["draft", "develop", "merge"]
ActorKind = Literal["human", "automation"]
Priority = Literal["high", "normal", "low"]

PHASES: tuple[str, ...] = ("draft", "develop", "merge")
ACTOR_KINDS: tuple[str, ...] = ("human", "automation")
PRIORITIES: tuple[str, ...] = ("high", "normal", "low")
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}

SCHEMA_VERSION = 1

_cfg = get_config()
REPO_ROOT = _cfg.root
DEFAULT_QUEUE_PATH = _cfg.paths.queue
DEFAULT_STATE_PATH = _cfg.paths.queue_state
DEFAULT_LOCK_PATH = _cfg.paths.queue_lock
DEFAULT_TRIAGE_LOG_PATH = _cfg.paths.triage_log
DEFAULT_SEED_PATH = _cfg.paths.seed
DEFAULT_NIGHT_STATE_PATH = _cfg.paths.night_state


class QueueValidationError(ValueError):
    """Invalid queue request input (exit 2)."""


@dataclass(frozen=True)
class QueueRequest:
    request_id: str
    issue: int
    phase: Phase
    source: str
    actor_kind: ActorKind
    priority: Priority
    requested_at: str
    requested_by: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "issue": self.issue,
            "phase": self.phase,
            "source": self.source,
            "actor_kind": self.actor_kind,
            "priority": self.priority,
            "requested_at": self.requested_at,
            "requested_by": list(self.requested_by),
        }


@dataclass
class EnqueueResult:
    request_id: str
    created: bool
    merged: bool = False
    message: str = ""


@dataclass
class QueueSnapshot:
    schema_version: int
    revision: int
    active_order: list[str]
    completed_request_ids: list[str]
    last_triaged_revision: int
    last_issue: int | None
    halt: bool
    halt_reason: str | None
    requests: dict[str, QueueRequest]
    request_meta: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def active_requests(self) -> list[QueueRequest]:
        return [self.requests[rid] for rid in self.active_order if rid in self.requests]


def _parse_requested_at(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QueueValidationError(f"invalid requested_at: {value!r}") from exc
    if dt.tzinfo is None:
        raise QueueValidationError("requested_at must include timezone")
    return dt


def validate_request_fields(
    *,
    issue: int,
    phase: str,
    source: str,
    actor_kind: str,
    priority: str,
    requested_at: str,
    requested_by: list[str] | tuple[str, ...],
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    if extra:
        raise QueueValidationError(f"unknown fields: {sorted(extra)}")
    if not isinstance(issue, int) or isinstance(issue, bool) or issue <= 0:
        raise QueueValidationError(f"issue must be positive int, got {issue!r}")
    if phase not in PHASES:
        raise QueueValidationError(f"unknown phase: {phase!r}")
    if actor_kind not in ACTOR_KINDS:
        raise QueueValidationError(f"unknown actor_kind: {actor_kind!r}")
    if priority not in PRIORITIES:
        raise QueueValidationError(f"unknown priority: {priority!r}")
    if not isinstance(source, str) or not source.strip():
        raise QueueValidationError("source must be non-empty string")
    if not isinstance(requested_by, (list, tuple)) or not requested_by:
        raise QueueValidationError("requested_by must be non-empty list")
    if any(not isinstance(x, str) or not x.strip() for x in requested_by):
        raise QueueValidationError("requested_by entries must be non-empty strings")
    _parse_requested_at(requested_at)
    if request_id is not None:
        try:
            uuid.UUID(request_id)
        except ValueError as exc:
            raise QueueValidationError(f"invalid request_id: {request_id!r}") from exc


def request_from_dict(data: dict[str, Any]) -> QueueRequest:
    known = {
        "request_id",
        "issue",
        "phase",
        "source",
        "actor_kind",
        "priority",
        "requested_at",
        "requested_by",
    }
    extra = {k: v for k, v in data.items() if k not in known}
    validate_request_fields(
        issue=data.get("issue"),  # type: ignore[arg-type]
        phase=str(data.get("phase", "")),
        source=str(data.get("source", "")),
        actor_kind=str(data.get("actor_kind", "")),
        priority=str(data.get("priority", "")),
        requested_at=str(data.get("requested_at", "")),
        requested_by=data.get("requested_by") or [],
        request_id=str(data.get("request_id", "")) or None,
        extra=extra,
    )
    return QueueRequest(
        request_id=str(data["request_id"]),
        issue=int(data["issue"]),
        phase=data["phase"],  # type: ignore[arg-type]
        source=str(data["source"]),
        actor_kind=data["actor_kind"],  # type: ignore[arg-type]
        priority=data["priority"],  # type: ignore[arg-type]
        requested_at=str(data["requested_at"]),
        requested_by=tuple(str(x) for x in data["requested_by"]),
    )


def default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "active_order": [],
        "completed_request_ids": [],
        "last_triaged_revision": 0,
        "last_issue": None,
        "halt": False,
        "halt_reason": None,
        "request_meta": {},
        "seeded_keys": [],
    }


def higher_priority(a: str, b: str) -> str:
    return a if PRIORITY_RANK.get(a, 99) <= PRIORITY_RANK.get(b, 99) else b


class QueueStore:
    def __init__(
        self,
        *,
        queue_path: Path | None = None,
        state_path: Path | None = None,
        lock_path: Path | None = None,
    ) -> None:
        # Tests / isolated runners may set ISSUESMITH_QUEUE_DIR so defaults never
        # touch the production jobs/ and logs/ paths under the repo root.
        env_dir = os.environ.get("ISSUESMITH_QUEUE_DIR")
        if env_dir:
            base = Path(env_dir)
            self.queue_path = queue_path or base / "issuesmith-queue.jsonl"
            self.state_path = state_path or base / "issuesmith-queue-state.json"
            self.lock_path = lock_path or base / "issuesmith-queue.lock"
        else:
            self.queue_path = queue_path or DEFAULT_QUEUE_PATH
            self.state_path = state_path or DEFAULT_STATE_PATH
            self.lock_path = lock_path or DEFAULT_LOCK_PATH
        # Separate from lock_path so dispatch_one can hold this while calling
        # snapshot()/complete()/set_last_issue() which acquire lock() on another FD.
        self.dispatch_lock_path = self.lock_path.with_name("dispatch.lock")

    @contextmanager
    def lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def dispatch_lock(self) -> Iterator[None]:
        """Exclusive lock for the dispatch critical section (separate file from lock())."""
        self.dispatch_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.dispatch_lock_path, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _load_state_unlocked(self) -> dict[str, Any]:
        state = default_state()
        if not self.state_path.exists():
            return state
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return state
        if isinstance(payload, dict):
            state.update(payload)
        state.setdefault("schema_version", SCHEMA_VERSION)
        state.setdefault("revision", 0)
        state.setdefault("active_order", [])
        state.setdefault("completed_request_ids", [])
        state.setdefault("last_triaged_revision", 0)
        state.setdefault("last_issue", None)
        state.setdefault("halt", False)
        state.setdefault("halt_reason", None)
        state.setdefault("request_meta", {})
        state.setdefault("seeded_keys", [])
        return state

    def _save_state_unlocked(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f"{self.state_path.name}.tmp.{os.getpid()}")
        payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.state_path)

    def _append_request_unlocked(self, request: QueueRequest) -> None:
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(request.to_dict(), ensure_ascii=False) + "\n"
        with open(self.queue_path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _read_requests_unlocked(self) -> dict[str, QueueRequest]:
        requests: dict[str, QueueRequest] = {}
        if not self.queue_path.exists():
            return requests
        for lineno, raw in enumerate(self.queue_path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print(f"warning: skipping malformed queue line {lineno}", flush=True)
                continue
            if not isinstance(data, dict):
                continue
            try:
                req = request_from_dict(data)
            except QueueValidationError:
                print(f"warning: skipping invalid queue line {lineno}", flush=True)
                continue
            requests[req.request_id] = req
        return requests

    def snapshot(self) -> QueueSnapshot:
        with self.lock():
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> QueueSnapshot:
        state = self._load_state_unlocked()
        requests = self._read_requests_unlocked()
        completed = set(state.get("completed_request_ids") or [])
        active_order = [
            rid for rid in (state.get("active_order") or []) if rid in requests and rid not in completed
        ]
        for rid, req in requests.items():
            if rid not in completed and rid not in active_order:
                active_order.append(rid)
        return QueueSnapshot(
            schema_version=int(state.get("schema_version", SCHEMA_VERSION)),
            revision=int(state.get("revision", 0)),
            active_order=list(active_order),
            completed_request_ids=list(state.get("completed_request_ids") or []),
            last_triaged_revision=int(state.get("last_triaged_revision", 0)),
            last_issue=state.get("last_issue"),
            halt=bool(state.get("halt", False)),
            halt_reason=state.get("halt_reason"),
            requests=requests,
            request_meta=dict(state.get("request_meta") or {}),
        )

    def enqueue(
        self,
        *,
        issue: int,
        phase: str,
        source: str,
        actor_kind: str,
        priority: str,
        requested_by: list[str],
        requested_at: str | None = None,
        request_id: str | None = None,
        force: bool = False,
        seed_key: str | None = None,
    ) -> EnqueueResult:
        at = requested_at or datetime.now().astimezone().isoformat()
        validate_request_fields(
            issue=issue,
            phase=phase,
            source=source,
            actor_kind=actor_kind,
            priority=priority,
            requested_at=at,
            requested_by=requested_by,
        )
        with self.lock():
            state = self._load_state_unlocked()
            if seed_key:
                seeded = list(state.get("seeded_keys") or [])
                if seed_key in seeded:
                    snap = self._snapshot_unlocked()
                    for rid in snap.active_order:
                        req = snap.requests[rid]
                        if req.issue == issue and req.phase == phase:
                            return EnqueueResult(
                                request_id=rid,
                                created=False,
                                merged=False,
                                message="seed already present",
                            )
                    return EnqueueResult(
                        request_id="",
                        created=False,
                        merged=False,
                        message="seed already recorded",
                    )

            snap = self._snapshot_unlocked()
            for rid in snap.active_order:
                existing = snap.requests[rid]
                if existing.issue == issue and existing.phase == phase:
                    meta = dict(state.get("request_meta") or {})
                    entry = dict(meta.get(rid) or {})
                    by = list(dict.fromkeys([*existing.requested_by, *requested_by]))
                    # Also merge previous meta requested_by if present
                    if isinstance(entry.get("requested_by"), list):
                        by = list(dict.fromkeys([*entry["requested_by"], *by]))
                    new_priority = higher_priority(
                        str(entry.get("priority") or existing.priority), priority
                    )
                    entry["requested_by"] = by
                    entry["priority"] = new_priority
                    if force:
                        entry["force"] = True
                    meta[rid] = entry
                    state["request_meta"] = meta
                    state["revision"] = int(state.get("revision", 0)) + 1
                    if seed_key:
                        seeded = list(state.get("seeded_keys") or [])
                        if seed_key not in seeded:
                            seeded.append(seed_key)
                            state["seeded_keys"] = seeded
                    self._save_state_unlocked(state)
                    return EnqueueResult(
                        request_id=rid,
                        created=False,
                        merged=True,
                        message="merged into existing request",
                    )

            rid = request_id or str(uuid.uuid4())
            req = QueueRequest(
                request_id=rid,
                issue=issue,
                phase=phase,  # type: ignore[arg-type]
                source=source,
                actor_kind=actor_kind,  # type: ignore[arg-type]
                priority=priority,  # type: ignore[arg-type]
                requested_at=at,
                requested_by=tuple(requested_by),
            )
            self._append_request_unlocked(req)
            order = list(state.get("active_order") or [])
            order.append(rid)
            state["active_order"] = order
            state["revision"] = int(state.get("revision", 0)) + 1
            meta = dict(state.get("request_meta") or {})
            if force:
                meta[rid] = {"force": True}
                state["request_meta"] = meta
            if seed_key:
                seeded = list(state.get("seeded_keys") or [])
                if seed_key not in seeded:
                    seeded.append(seed_key)
                    state["seeded_keys"] = seeded
            self._save_state_unlocked(state)
            return EnqueueResult(request_id=rid, created=True, merged=False, message="enqueued")

    def effective_request(self, snap: QueueSnapshot, request_id: str) -> QueueRequest | None:
        req = snap.requests.get(request_id)
        if req is None:
            return None
        meta = snap.request_meta.get(request_id) or {}
        by = meta.get("requested_by")
        priority = meta.get("priority")
        return QueueRequest(
            request_id=req.request_id,
            issue=req.issue,
            phase=req.phase,
            source=req.source,
            actor_kind=req.actor_kind,
            priority=priority if priority in PRIORITIES else req.priority,  # type: ignore[arg-type]
            requested_at=req.requested_at,
            requested_by=tuple(by) if isinstance(by, list) else req.requested_by,
        )

    def is_force(self, snap: QueueSnapshot, request_id: str) -> bool:
        meta = snap.request_meta.get(request_id) or {}
        return bool(meta.get("force"))

    def complete(self, request_id: str, outcome: str, *, extra_meta: dict[str, Any] | None = None) -> None:
        with self.lock():
            state = self._load_state_unlocked()
            completed = list(state.get("completed_request_ids") or [])
            if request_id not in completed:
                completed.append(request_id)
            state["completed_request_ids"] = completed
            order = [rid for rid in (state.get("active_order") or []) if rid != request_id]
            state["active_order"] = order
            meta = dict(state.get("request_meta") or {})
            entry = dict(meta.get(request_id) or {})
            entry["outcome"] = outcome
            if extra_meta:
                entry.update(extra_meta)
            meta[request_id] = entry
            state["request_meta"] = meta
            state["revision"] = int(state.get("revision", 0)) + 1
            self._save_state_unlocked(state)

    def replace_order(self, expected_revision: int, request_ids: list[str]) -> bool:
        with self.lock():
            state = self._load_state_unlocked()
            if int(state.get("revision", 0)) != expected_revision:
                return False
            completed = set(state.get("completed_request_ids") or [])
            active = [rid for rid in request_ids if rid not in completed]
            current = {rid for rid in (state.get("active_order") or []) if rid not in completed}
            if set(active) != current:
                missing = current - set(active)
                if missing:
                    return False
            state["active_order"] = active
            state["revision"] = expected_revision + 1
            # Triage produced this new revision; do not re-triage until another mutation.
            state["last_triaged_revision"] = state["revision"]
            self._save_state_unlocked(state)
            return True

    def set_halt(self, halt: bool, reason: str | None = None) -> None:
        with self.lock():
            state = self._load_state_unlocked()
            state["halt"] = halt
            state["halt_reason"] = reason if halt else None
            self._save_state_unlocked(state)

    def clear_halt(self) -> None:
        self.set_halt(False, None)

    def set_last_issue(self, issue: int | None) -> None:
        with self.lock():
            state = self._load_state_unlocked()
            state["last_issue"] = issue
            self._save_state_unlocked(state)

    def mark_triaged(self, revision: int) -> None:
        with self.lock():
            state = self._load_state_unlocked()
            state["last_triaged_revision"] = revision
            self._save_state_unlocked(state)

    def update_meta(self, request_id: str, patch: dict[str, Any]) -> None:
        with self.lock():
            state = self._load_state_unlocked()
            meta = dict(state.get("request_meta") or {})
            entry = dict(meta.get(request_id) or {})
            entry.update(patch)
            meta[request_id] = entry
            state["request_meta"] = meta
            self._save_state_unlocked(state)
