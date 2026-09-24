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
    force: bool = False,
) -> int:
    """Resume a stalled issue.

    from_step: resume from a specific DAG step (ghdag dag recover equivalent)
    phase: redispatch to phase (label reset + queue re-enqueue)
    workflow: ghdag workflow name passed to ``dag recover --workflow``; defaults to the
        stem of ``paths.workflow`` so hosts with several workflows resolve (#3590)
    handler: ghdag handler name; defaults to the handler owning ``from_step``
    mark_done: steps to mark as success before recovering (requires from_step)
    force: with from_step, re-run the step and everything downstream even if they succeeded
        (done markers and results are cleared; without it recover only re-runs failed /
        pending steps and succeeded ones keep their results)

    Always releases in_flight before acting.
    """
    if from_step is None and phase is None:
        raise ValueError("resume() requires from_step= or phase=")

    QueueStore().remove_in_flight(issue)

    if from_step is not None:
        return _resume_from_step(
            issue, from_step, workflow=workflow, handler=handler, mark_done=mark_done, force=force
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



def _phase_for_handler(handler: str) -> str | None:
    cfg = get_config()
    for ph in cfg.phases:
        if getattr(ph, "handler", "") == handler:
            return ph.name
    fallback = {"impl": "develop", "merge": "merge", "draft": "draft", "sub": "sub"}
    phase = fallback.get(handler)
    return phase if phase and any(ph.name == phase for ph in cfg.phases) else None


def _downstream_steps(steps: "list[StepStatus]", from_step: str) -> "list[StepStatus]":
    """``from_step`` and every step that (transitively) depends on it."""
    by_name = {s.step_name: s for s in steps}
    start = by_name.get(from_step)
    if start is None:
        return []
    selected = {start.uuid}
    changed = True
    while changed:
        changed = False
        for s in steps:
            if s.uuid not in selected and any(d in selected for d in s.depends):
                selected.add(s.uuid)
                changed = True
    return [s for s in steps if s.uuid in selected]


def _clear_stale_results(steps: "list[StepStatus]", from_step: str, *, force: bool = False) -> None:
    """Delete result files of the steps about to re-run.

    ghdag keeps a non-empty result file and discards the rerun's stdout
    (``result_finalize=preserve_nonempty``), so a re-executed step was judged by its
    previous PIPELINE_STATUS (sumipan/nexus#3638). Clear them before ``dag recover``.

    Only steps that recover will actually re-run are touched: succeeded steps keep their
    results (downstream steps read them) unless ``force`` also resets their done markers.
    """
    cfg = get_config()
    for step in _downstream_steps(steps, from_step):
        if step.status == "success" and not force:
            continue
        if not step.result_path:
            continue
        path = Path(step.result_path)
        if not path.is_absolute():
            path = cfg.root / step.result_path
        if path.exists():
            try:
                path.unlink()
                print(f"cleared result: {step.step_name} ({path.name})", file=sys.stderr)
            except OSError as exc:
                print(f"warning: could not clear result for {step.step_name}: {exc}", file=sys.stderr)


def _restore_running_state(issue: int, handler: str) -> None:
    """Put the issue back into the state a running DAG expects.

    When a DAG terminates the observe policy releases in_flight and projects
    ``<phase>-ready`` (#3662). ``resume --from`` restarts the same DAG, so the
    ``<phase>-running`` label (the P3 finalizer requires it) and the in_flight entry
    must come back; before this they were restored by hand on every recovery
    (sumipan/nexus#3627 / #3696, 2026-09-24).
    """
    from issuesmith.queue import READY_LABEL, RUNNING_LABEL, issue_target_meta, resolve_engine

    phase = _phase_for_handler(handler)
    if phase is None:
        return
    running = RUNNING_LABEL.get(phase, "")
    ready = READY_LABEL.get(phase, "")
    cfg = get_config()
    try:
        client = get_forge(repo=cfg.repo)
        issue_data = client.issue_get(issue, fields=["state", "labels", "body", "number"])
        if running:
            client.issue_update(issue, labels_add=[running], labels_remove=[ready] if ready else [])
            print(f"restored label: {running}", file=sys.stderr)
        target_repo, allow_paths = issue_target_meta(issue_data)
        role = next((ph.role for ph in cfg.phases if ph.name == phase), "implementation")
        QueueStore().add_in_flight(
            issue,
            resolve_engine(phase),
            role=role,
            allow_paths=allow_paths,
            target_repo=target_repo or None,
        )
        print(f"restored in_flight: #{issue} ({phase})", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - the DAG is already recovering; report, do not fail
        print(f"warning: could not restore running state for #{issue}: {exc}", file=sys.stderr)


def _reset_done_markers(steps: "list[StepStatus]", from_step: str) -> None:
    """``--force``: forget the success of from_step and everything downstream."""
    done_dir = get_config().paths.done_dir
    for step in _downstream_steps(steps, from_step):
        marker = Path(done_dir) / step.uuid
        if marker.exists():
            marker.unlink()
            print(f"reset done marker: {step.step_name} ({step.uuid})", file=sys.stderr)


def _recover_and_restore(
    issue: int,
    handler: str,
    from_step: str,
    workflow: str,
    steps: "list[StepStatus]",
    *,
    force: bool = False,
) -> int:
    if steps:
        if force:
            _reset_done_markers(steps, from_step)
        _clear_stale_results(steps, from_step, force=force)
    rc = _run_ghdag_recover(issue, handler, from_step, workflow)
    if rc == 0 and steps:
        _restore_running_state(issue, handler)
    return rc


def _resume_from_step(
    issue: int,
    from_step: str,
    *,
    workflow: str | None = None,
    handler: str | None = None,
    mark_done: list[str] | None = None,
    force: bool = False,
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
            # Report all blockers so option 2 keeps the requested --mark-done steps.
            print(_format_blockers_message(issue, from_step, blockers), file=sys.stderr)
            return 1

        # All clear: write done markers then recover
        for name in mark_done:
            _write_step_mark_done(by_name[name])

        return _recover_and_restore(issue, handler, from_step, workflow, steps, force=force)

    blockers = _upstream_blockers(steps, from_step)
    if blockers:
        print(_format_blockers_message(issue, from_step, blockers), file=sys.stderr)
        return 1

    return _recover_and_restore(issue, handler, from_step, workflow, steps, force=force)


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
