"""GitHub API rate-limit brake (#3769).

ghdag dispatcher appends a ``github_rate_limit`` record to ``jobs/audit.jsonl`` for each
API response (``X-RateLimit-Remaining`` / ``X-RateLimit-Reset``). This module reads the
latest record and decides whether the remaining budget is too low to start new work.
``ForgePort.get_rate_limit()`` is not used because the local forge returns ``None``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_RATE_LIMIT_EVENT = "github_rate_limit"
_RATE_LIMIT_MARKER = b'"github_rate_limit"'
_CHUNK_SIZE = 64 * 1024
_NOTIFIED_KEY = "github_api_notified"


@dataclass(frozen=True)
class GitHubApiState:
    remaining: int
    reset_at: datetime  # UTC (audit.jsonl ``reset`` is UNIX seconds)
    observed_at: datetime


def _iter_lines_reversed(path: Path):
    """Yield the file's lines (bytes, without newline) from the last one to the first."""
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        pos = fh.tell()
        tail = b""
        while pos > 0:
            step = min(_CHUNK_SIZE, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + tail
            lines = buf.split(b"\n")
            tail = lines[0]
            for line in reversed(lines[1:]):
                yield line
        yield tail


def _parse_state(record: dict) -> GitHubApiState | None:
    try:
        remaining = int(record["remaining"])
        reset_at = datetime.fromtimestamp(int(record["reset"]), tz=timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    observed_at = reset_at
    ts = record.get("timestamp")
    if isinstance(ts, str):
        try:
            observed_at = datetime.fromisoformat(ts)
        except ValueError:
            pass
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return GitHubApiState(
        remaining=remaining,
        reset_at=reset_at,
        observed_at=observed_at.astimezone(timezone.utc),
    )


def read_github_api_state(audit_path: Path) -> GitHubApiState | None:
    """Return the latest ``github_rate_limit`` record in audit.jsonl.

    Scans from the end (the file is append-only and grows without bound). Returns ``None``
    when the file is missing, has no such record, or the latest record is malformed.
    Undecodable lines (e.g. a partially appended tail) are skipped.
    """
    try:
        for raw in _iter_lines_reversed(Path(audit_path)):
            if _RATE_LIMIT_MARKER not in raw:
                continue
            try:
                record = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(record, dict) or record.get("event") != _RATE_LIMIT_EVENT:
                continue
            return _parse_state(record)
    except OSError:
        return None
    return None


def is_github_api_low(
    state: GitHubApiState | None,
    min_remaining: int,
    now: datetime,
) -> bool:
    """``state`` None → False. ``reset_at`` in the past → False. ``remaining < min_remaining`` → True."""
    if state is None:
        return False
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if state.reset_at <= now:
        return False
    return state.remaining < min_remaining


def read_github_api_notified(quota_state_path: Path) -> bool:
    """Return ``resources.github_api_notified`` from quota-gate.json (False when absent)."""
    from ghdag.quota import QuotaGate

    gate = QuotaGate(state_path=quota_state_path)
    try:
        with gate._lock(exclusive=False):
            state = gate._load_state_unlocked()
    except (OSError, ValueError):
        return False
    resources = state.get("resources")
    if not isinstance(resources, dict):
        return False
    return bool(resources.get(_NOTIFIED_KEY, False))


def write_github_api_notified(quota_state_path: Path, value: bool) -> bool:
    """Read-modify-write ``resources.github_api_notified`` under the QuotaGate lock.

    Returns True only when the flag actually changed (compare-and-set, so concurrent
    observers report a transition once). Unknown fields are preserved by
    ``QuotaGate._load_state_unlocked``.
    """
    from ghdag.quota import QuotaGate

    gate = QuotaGate(state_path=quota_state_path)
    with gate._lock(exclusive=True):
        state = gate._load_state_unlocked()
        resources = state.get("resources")
        if not isinstance(resources, dict):
            resources = {}
        if bool(resources.get(_NOTIFIED_KEY, False)) == value:
            return False
        resources[_NOTIFIED_KEY] = value
        state["resources"] = resources
        gate._write_state_unlocked(state)
        return True
