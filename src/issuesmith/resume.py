"""Central resume entry point (#3509).

resume(issue, *, from_step=None, phase=None) unifies recover/redispatch:
  - from_step: ghdag dag recover + in_flight release
  - phase: in_flight release + label reset + queue re-enqueue
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ghdag.forge import get_forge

from issuesmith.config import get_config
from issuesmith.queue import apply_redispatch_labels, handler_for_failed_step
from issuesmith.queue_store import QueueStore

if TYPE_CHECKING:
    from ghdag.status import StepStatus


def resume(
    issue: int,
    *,
    from_step: str | None = None,
    phase: str | None = None,
    workflow: str | None = None,
    handler: str | None = None,
    mark_done: list[str] | None = None,
) -> int:
    """Resume a stalled issue.

    from_step: resume from a specific DAG step (ghdag dag recover equivalent)
    phase: redispatch to phase (label reset + queue re-enqueue)
    workflow: ghdag workflow name passed to ``dag recover --workflow``; defaults to the
        stem of ``paths.workflow`` so hosts with several workflows resolve (#3590)
    handler: ghdag handler name; defaults to the handler owning ``from_step``
    mark_done: steps to mark as success before recovering (requires from_step)

    Always releases in_flight before acting.
    """
    if from_step is None and phase is None:
        raise ValueError("resume() requires from_step= or phase=")

    QueueStore().remove_in_flight(issue)

    if from_step is not None:
        return _resume_from_step(
            issue, from_step, workflow=workflow, handler=handler, mark_done=mark_done
        )

    assert phase is not None
    return _resume_phase(issue, phase)


def _generation_keys_available() -> bool:
    from issuesmith.recovery import _generation_keys_available as _gka
    return _gka()


def _default_workflow_name() -> str:
    return get_config().paths.workflow.stem


def _run_ghdag_recover(
    issue: int, handler: str, from_step: str | None, workflow: str | None = None
) -> int:
    from issuesmith.recovery import _run_ghdag_recover as _rgr
    return _rgr(issue, handler, from_step, workflow=workflow)


def _load_step_statuses(
    issue: int, handler: str, workflow: str
) -> list[StepStatus] | None:
    """Return steps for the current DAG run, or None on I/O error (fail closed)."""
    from ghdag import status as ghdag_status
    from ghdag.dag.recover import running_uuids_from_queue_dir

    try:
        cfg = get_config()
        running = running_uuids_from_queue_dir(cfg.paths.exec_jsonl.parent)
        result = ghdag_status.issue_status(
            issue,
            handler=handler,
            workflow=workflow,
            exec_jsonl_path=cfg.paths.exec_jsonl,
            state_dir=cfg.root / ".pipeline-state",
            done_dir=cfg.paths.done_dir,
            running_uuids=running,
        )
        return result.steps
    except (OSError, ValueError) as exc:
        print(f"error: cannot load step statuses: {exc}", file=sys.stderr)
        return None


def _upstream_blockers(
    steps: list[StepStatus], from_step: str
) -> list[StepStatus]:
    """Return ancestors of from_step whose status blocks re-execution.

    Returned in topological order (upstream first). Statuses that do NOT
    block: success, pending, running.
    """
    if not steps:
        return []

    by_name = {s.step_name: s for s in steps}
    by_uuid = {s.uuid: s for s in steps}

    start = by_name.get(from_step)
    if start is None:
        return []

    # BFS to collect all ancestor UUIDs
    ancestor_uuids: set[str] = set()
    frontier = list(start.depends)
    while frontier:
        uid = frontier.pop()
        if uid in ancestor_uuids:
            continue
        ancestor_uuids.add(uid)
        step = by_uuid.get(uid)
        if step:
            frontier.extend(step.depends)

    ancestors = [s for s in steps if s.uuid in ancestor_uuids]
    if not ancestors:
        return []

    # Topological sort (upstream first) via Kahn's algorithm
    adj: dict[str, list[str]] = {s.uuid: [] for s in ancestors}
    in_deg: dict[str, int] = {s.uuid: 0 for s in ancestors}
    for s in ancestors:
        for dep_uuid in s.depends:
            if dep_uuid in ancestor_uuids:
                adj[dep_uuid].append(s.uuid)
                in_deg[s.uuid] += 1

    by_uuid_anc = {s.uuid: s for s in ancestors}
    queue = [by_uuid_anc[u] for u in ancestor_uuids if in_deg[u] == 0]
    topo: list[StepStatus] = []
    while queue:
        step = queue.pop(0)
        topo.append(step)
        for dep_uid in adj.get(step.uuid, []):
            in_deg[dep_uid] -= 1
            if in_deg[dep_uid] == 0:
                queue.append(by_uuid_anc[dep_uid])

    _non_blocking = {"success", "pending", "running"}
    return [s for s in topo if s.status not in _non_blocking]


def _format_blockers_message(
    issue: int, from_step: str, blockers: list[StepStatus]
) -> str:
    blocker_list = ", ".join(f"{b.step_name} ({b.status})" for b in blockers)
    lines = [
        f"cannot resume #{issue} from {from_step!r}: "
        f"upstream step(s) not succeeded: {blocker_list}"
    ]

    roots = [b for b in blockers if b.status != "dep_failed"]
    dep_failed_blockers = [b for b in blockers if b.status == "dep_failed"]

    for root in roots:
        lines.append(
            f"  option 1 (re-run the failed step):   "
            f"python3 -m issuesmith resume {issue} --from {root.step_name}"
        )

    if dep_failed_blockers:
        opt2_from = dep_failed_blockers[0].step_name
    else:
        opt2_from = from_step

    mark_done_flags = "".join(f" --mark-done {r.step_name}" for r in roots)
    lines.append(
        f"  option 2 (its output already exists): "
        f"python3 -m issuesmith resume {issue} --from {opt2_from}{mark_done_flags}"
    )

    return "\n".join(lines)


def _write_step_mark_done(step: StepStatus) -> None:
    from ghdag.core.vocabulary import DONE_SUCCESS
    from ghdag.io.done import mark_done as _mark_done_file

    cfg = get_config()
    _mark_done_file(cfg.paths.done_dir, step.uuid, DONE_SUCCESS)
    print(f"marked done: {step.step_name} ({step.uuid})", file=sys.stderr)

    if step.result_path:
        p = Path(step.result_path)
        if not p.is_absolute():
            p = cfg.root / step.result_path
        if not p.exists() or p.stat().st_size == 0:
            print(
                f"warning: result file for {step.step_name!r} is missing or empty: {p};"
                " downstream steps that read it may treat the step as skipped",
                file=sys.stderr,
            )


def _resume_from_step(
    issue: int,
    from_step: str,
    *,
    workflow: str | None = None,
    handler: str | None = None,
    mark_done: list[str] | None = None,
) -> int:
    handler = handler or handler_for_failed_step(from_step, set())
    workflow = workflow or _default_workflow_name()
    if not _generation_keys_available():
        print(
            f"ghdag dag recover is not available; cannot resume from step {from_step!r}",
            file=sys.stderr,
        )
        return 1

    steps = _load_step_statuses(issue, handler, workflow)
    if steps is None:
        return 1

    if mark_done is not None:
        by_name = {s.step_name: s for s in steps}
        by_uuid = {s.uuid: s for s in steps}

        # Collect ancestor UUIDs of from_step for validation
        start = by_name.get(from_step)
        ancestor_uuids: set[str] = set()
        if start:
            frontier = list(start.depends)
            while frontier:
                uid = frontier.pop()
                if uid in ancestor_uuids:
                    continue
                ancestor_uuids.add(uid)
                dep = by_uuid.get(uid)
                if dep:
                    frontier.extend(dep.depends)

        _markable = {"failed", "cancelled", "skipped"}
        for name in mark_done:
            step = by_name.get(name)
            if step is None:
                print(f"error: --mark-done {name!r}: step not found", file=sys.stderr)
                return 2
            if step.uuid not in ancestor_uuids:
                print(
                    f"error: --mark-done {name!r}: not an ancestor of {from_step!r}",
                    file=sys.stderr,
                )
                return 2
            if step.status not in _markable:
                print(
                    f"error: --mark-done {name!r}: status is {step.status!r},"
                    f" must be one of {sorted(_markable)}",
                    file=sys.stderr,
                )
                return 2

        # Check if remaining blockers survive after simulated mark_done
        blockers = _upstream_blockers(steps, from_step)
        mark_done_set = set(mark_done)
        remaining = [b for b in blockers if b.step_name not in mark_done_set]
        if remaining:
            print(_format_blockers_message(issue, from_step, remaining), file=sys.stderr)
            return 1

        # All clear: write done markers then recover
        for name in mark_done:
            _write_step_mark_done(by_name[name])

        return _run_ghdag_recover(issue, handler, from_step, workflow)

    blockers = _upstream_blockers(steps, from_step)
    if blockers:
        print(_format_blockers_message(issue, from_step, blockers), file=sys.stderr)
        return 1

    return _run_ghdag_recover(issue, handler, from_step, workflow)


def _resume_phase(issue: int, phase: str) -> int:
    client = get_forge(repo=get_config().repo)
    labels_data = client.issue_get(issue, fields=["labels"])
    current_labels = {
        lab.get("name")
        for lab in (labels_data.get("labels") or [])
        if isinstance(lab, dict) and lab.get("name")
    }
    apply_redispatch_labels(client, issue, phase, current_labels)  # type: ignore[arg-type]  # TODO(#3611)
    store = QueueStore()
    result = store.enqueue(
        issue=issue,
        phase=phase,
        source="resume",
        actor_kind="human",
        priority="high",
        requested_by=["resume"],
    )
    print(json.dumps({"request_id": result.request_id, "created": result.created}, ensure_ascii=False))
    return 0
