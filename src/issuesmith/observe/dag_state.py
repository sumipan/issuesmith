"""issuesmith.observe.dag_state — read DAG liveness from exec.jsonl and done/running markers."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DagStatus = Literal["running", "pending", "failed", "succeeded"]


@dataclass(frozen=True)
class DagState:
    issue: int
    key: str
    status: DagStatus
    failed_step: str = ""
    failed_uuid: str = ""
    failed_result_path: str = ""


def load_dag_states(
    exec_jsonl: Path,
    done_dir: Path,
    running_dir: Path,
) -> dict[int, DagState]:
    """Return a mapping of issue number to DagState.

    Reads exec.jsonl, done markers, and running markers — no GitHub API calls.
    """
    if not exec_jsonl.exists():
        return {}

    # Group rows by issue number; keep all rows in order.
    # For each issue, only the last idempotency_key (latest generation) is current.
    issue_last_key: dict[int, str] = {}
    issue_rows: dict[int, list[dict]] = {}

    from issuesmith.config import get_config as _get_config
    workflow_prefix = _get_config().label_namespace + ":"

    try:
        raw_text = exec_jsonl.read_text(encoding="utf-8")
    except OSError:
        return {}

    for raw in raw_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        key = str(row.get("idempotency_key", ""))
        if not key.startswith(workflow_prefix):
            continue
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            continue
        issue = _issue_from_key(key)
        if issue is None:
            continue
        issue_last_key[issue] = key
        if issue not in issue_rows:
            issue_rows[issue] = []
        issue_rows[issue].append(row)

    result: dict[int, DagState] = {}
    for issue, current_key in issue_last_key.items():
        rows = [r for r in issue_rows[issue] if r.get("idempotency_key") == current_key]
        state = _compute_dag_state(issue, current_key, rows, done_dir, running_dir)
        result[issue] = state

    return result


def _issue_from_key(key: str) -> int | None:
    parts = key.split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return None


def _step_name_from_row(row: dict) -> str:
    annotations = row.get("annotations") or {}
    if isinstance(annotations, dict):
        name = annotations.get("step_name")
        if name:
            return str(name)
    return str(row.get("uuid", ""))


def _compute_dag_state(
    issue: int,
    key: str,
    rows: list[dict],
    done_dir: Path,
    running_dir: Path,
) -> DagState:
    from ghdag.io.done import interpret_done, read_done_content

    if not rows:
        return DagState(issue=issue, key=key, status="pending")

    # First pass: collect per-step status
    step_results: dict[str, str] = {}  # uuid -> "running" | "success" | "failed" | "pending"

    for row in rows:
        uuid = str(row.get("uuid", ""))
        if not uuid:
            continue
        if (running_dir / f"{uuid}.json").exists():
            step_results[uuid] = "running"
            continue
        raw = read_done_content(done_dir, uuid)
        outcome = interpret_done(raw)
        if outcome is None:
            step_results[uuid] = "pending"
        elif outcome == "success":
            step_results[uuid] = "success"
        else:
            step_results[uuid] = "failed"

    # Check if any step is running
    if any(s == "running" for s in step_results.values()):
        return DagState(issue=issue, key=key, status="running")

    # Find first failed step (by order in rows)
    first_failed_uuid: str | None = None
    first_failed_step: str = ""
    first_failed_result: str = ""
    for row in rows:
        uuid = str(row.get("uuid", ""))
        if step_results.get(uuid) == "failed":
            first_failed_uuid = uuid
            first_failed_step = _step_name_from_row(row)
            first_failed_result = str(done_dir / uuid)
            break

    # Determine if any pending step has all dependencies succeeded (ready to run)
    # Step is "runnable-pending" if its depends are all success
    has_runnable_pending = False
    for row in rows:
        uuid = str(row.get("uuid", ""))
        if step_results.get(uuid) != "pending":
            continue
        depends = row.get("depends") or []
        if not isinstance(depends, list):
            depends = []
        if all(
            step_results.get(dep_uuid) == "success"
            for dep_uuid in depends
        ):
            has_runnable_pending = True
            break

    # has_any_pending
    has_any_pending = any(s == "pending" for s in step_results.values())

    # Priority order: running (handled above), then pending with runnable deps, then failed,
    # then pending (with unmet deps), then succeeded
    if has_runnable_pending:
        return DagState(issue=issue, key=key, status="pending")

    if first_failed_uuid is not None:
        return DagState(
            issue=issue,
            key=key,
            status="failed",
            failed_step=first_failed_step,
            failed_uuid=first_failed_uuid,
            failed_result_path=first_failed_result,
        )

    if has_any_pending:
        return DagState(issue=issue, key=key, status="pending")

    return DagState(issue=issue, key=key, status="succeeded")
