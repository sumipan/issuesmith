"""Centralized metrics JSONL event writers (#4422)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


def _utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parent_uuid() -> str:
    return os.environ.get("GHDAG_TASK_UUID", "").strip()


def append_event(path: Path, event: dict) -> None:
    """Append one metrics line with ``ts`` and optional ``parent_uuid``."""
    record = dict(event)
    record["ts"] = _utc_ts()
    parent = _parent_uuid()
    if parent:
        record["parent_uuid"] = parent
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


def _parse_idempotency_key(key: str) -> tuple[str, int] | None:
    parts = key.split(":")
    if len(parts) < 3 or parts[0] != "issuesmith":
        return None
    workflow = parts[1]
    issue_part = parts[2]
    if not issue_part.isdigit():
        return None
    return workflow, int(issue_part)


def _find_exec_row(exec_path: Path, task_uuid: str) -> dict | None:
    try:
        text = exec_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(row.get("uuid", "")) == task_uuid:
            return row
    return None


class _PathsLike(Protocol):
    @property
    def exec_jsonl(self) -> Path: ...

    @property
    def metrics(self) -> Path: ...


def record_step_started(paths: _PathsLike) -> None:
    """Write ``step_started`` when exec.jsonl has a matching GHDAG_TASK_UUID row."""
    task_uuid = _parent_uuid()
    if not task_uuid:
        return
    try:
        row = _find_exec_row(paths.exec_jsonl, task_uuid)
        if row is None:
            return
        annotations = row.get("annotations")
        if not isinstance(annotations, dict):
            return
        step = annotations.get("step_name")
        if not step:
            return
        parsed = _parse_idempotency_key(str(row.get("idempotency_key", "")))
        if parsed is None:
            return
        workflow, issue = parsed
        append_event(
            paths.metrics,
            {
                "event": "step_started",
                "issue": issue,
                "step": step,
                "workflow": workflow,
            },
        )
    except Exception:
        return


__all__ = ["append_event", "record_step_started"]
