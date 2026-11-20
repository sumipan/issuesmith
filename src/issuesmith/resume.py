"""Central resume entry point (#3509).

resume(issue, *, from_step=None, phase=None) unifies recover/redispatch:
  - from_step: ghdag dag recover + in_flight release
  - phase: in_flight release + label reset + queue re-enqueue
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ghdag.forge import get_forge

from issuesmith.config import get_config
from issuesmith.contract import parse_frontmatter_fields
from issuesmith.queue import apply_redispatch_labels, handler_for_failed_step, issue_target_meta
from issuesmith.queue_store import QueueStore
from issuesmith.worktree import WorktreeError, fetch_base_or_raise

_STALE_BASE_COMMENT_TEMPLATE = """\
## base_branch sync failed (rebase conflict)

`origin/{base_branch}` rebase conflict. Resolve manually and resume.

**Conflict files:**
```
{conflict_files}
```

PIPELINE_STATUS: STALE_BASE"""

_RESUME_WORKFLOW = "issuesmith"
_ORDER_PATH_RE = re.compile(r"(?:bash -o pipefail )?(\S+jobs/\S+\.md)")
_WORKTREE_RE = re.compile(r'worktree_path=([^\s"]+)')
_TARGET_WORKTREE_RE = re.compile(r'target_worktree_path=([^\s"]+)')


def _dispatch_records_for_handler(handler: str, issue: int) -> list[dict]:
    path = get_config().paths.exec_jsonl
    if not path.exists():
        return []
    prefix = f"{_RESUME_WORKFLOW}:{handler}:{issue}"
    records: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        key = str(rec.get("idempotency_key", ""))
        if key == prefix or key.startswith(f"{prefix}:"):
            records.append(rec)
    return records


def _frozen_orders_for_issue(issue: int) -> list[Path]:
    jobs_dir = get_config().root / "jobs"
    if not jobs_dir.is_dir():
        return []
    marker = f"issue_number={issue}"
    found: list[Path] = []
    for path in sorted(jobs_dir.glob("*-shell-order-*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if marker in text:
            found.append(path)
    return found


def _worktree_path_for_resume(issue: int, records: list[dict]) -> Path | None:
    root = get_config().root

    def _path_from_match(raw: str) -> Path:
        path = Path(raw.strip())
        if not path.is_absolute():
            path = root / raw.strip()
        return path

    for rec in records:
        command = str(rec.get("command", ""))
        for pattern in (_WORKTREE_RE, _TARGET_WORKTREE_RE):
            m = pattern.search(command)
            if m:
                return _path_from_match(m.group(1))
        order_match = _ORDER_PATH_RE.search(command)
        if order_match:
            order_path = Path(order_match.group(1))
            if not order_path.is_absolute():
                order_path = root / order_path
            if order_path.exists():
                text = order_path.read_text(encoding="utf-8")
                for pattern in (_WORKTREE_RE, _TARGET_WORKTREE_RE):
                    m = pattern.search(text)
                    if m:
                        return _path_from_match(m.group(1))
    for order in _frozen_orders_for_issue(issue):
        text = order.read_text(encoding="utf-8")
        for pattern in (_WORKTREE_RE, _TARGET_WORKTREE_RE):
            m = pattern.search(text)
            if m:
                return _path_from_match(m.group(1))
    return None


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

    caller_step = from_step or phase or "resume"
    rc = _catch_up_base_before_resume(issue, caller_step=caller_step)
    if rc != 0:
        return rc

    store = QueueStore()
    store.remove_in_flight(issue)

    if from_step is not None:
        return _resume_from_step(
            issue, from_step, workflow=workflow, handler=handler, mark_done=mark_done, force=force
        )

    assert phase is not None
    return _resume_phase(issue, phase)


def _catch_up_base_before_resume(issue: int, *, caller_step: str) -> int:
    """Fetch base and rebase worktree onto it before resuming; fail closed on conflict."""
    from issuesmith.andon import Andon as FullAndon
    from issuesmith.andon import raise_andon
    from issuesmith.worktree import ensure_base_included

    cfg = get_config()
    try:
        client = get_forge(repo=cfg.repo)
    except Exception as exc:
        print(f"warning: resume base catch-up skipped: {exc}", file=sys.stderr)
        return 0

    try:
        issue_data = client.issue_get(issue, fields=["body", "number"])
    except Exception as exc:
        print(f"warning: resume base catch-up skipped: {exc}", file=sys.stderr)
        return 0

    meta = parse_frontmatter_fields(str(issue_data.get("body") or ""))
    base_branch = str(meta.get("base_branch") or "main").strip() or "main"
    target_repo, _paths = issue_target_meta(issue_data)

    records: list[dict] = []
    handlers: set[str] = set()
    for ph in cfg.phases:
        handler = getattr(ph, "handler", None)
        if isinstance(handler, str) and handler:
            handlers.add(handler)
    for handler in sorted(handlers):
        records.extend(_dispatch_records_for_handler(handler, issue))
    worktree = _worktree_path_for_resume(issue, records)
    if worktree is None or not worktree.is_dir():
        return 0

    top = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if top.returncode != 0:
        print("warning: resume base catch-up skipped: not a git worktree", file=sys.stderr)
        return 0
    repo_dir = Path(top.stdout.strip())

    try:
        if target_repo:
            fetch_base_or_raise(repo_dir, base_branch, remote=target_repo)
        else:
            fetch_base_or_raise(repo_dir, base_branch)
    except WorktreeError as exc:
        print(f"error: resume base fetch failed: {exc}", file=sys.stderr)
        return 1

    step_result = ensure_base_included(
        worktree,
        base_branch,
        client,
        issue,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
        caller_step=caller_step,
    )
    if step_result is None:
        return 0
    if getattr(step_result, "status", "done") != "andon" or step_result.andon is None:
        print("error: resume base catch-up failed", file=sys.stderr)
        return 1

    spec = step_result.andon
    workflow = cfg.paths.workflow.stem
    raise_andon(
        client,
        FullAndon(
            id=f"{workflow}:{issue}:{caller_step}:0",
            kind=spec.kind,
            issue=issue,
            step=caller_step,
            summary=spec.summary,
            rule_id=spec.rule_id,
            options=list(spec.options),
        ),
    )
    return 1


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
    from ghdag.dag import running_uuids_from_queue_dir

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


def _exec_step_handlers(issue: int) -> list[tuple[str, str]]:
    """(handler, step_name) of every exec.jsonl row keyed ``issuesmith:<handler>:<issue>[:<gen>]``."""
    path = get_config().paths.exec_jsonl
    if not path.exists():
        return []
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        parts = str(rec.get("idempotency_key", "")).split(":")
        if len(parts) < 3 or parts[0] != "issuesmith" or parts[2] != str(issue):
            continue
        annotations = rec.get("annotations")
        step = annotations.get("step_name") if isinstance(annotations, dict) else None
        if isinstance(step, str) and step:
            pairs.append((parts[1], step))
    return pairs


def _infer_handler_from_exec(issue: int, step: str) -> str | None:
    """Handler whose DAG ran ``step`` for ``issue`` according to exec.jsonl.

    The workflow YAML table maps m1 / m2 to merge, but the host may run them inside the
    impl DAG; the exec record says which DAG actually has the step.
    """
    return next((h for h, s in _exec_step_handlers(issue) if s == step), None)


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
    from ghdag.dag import DONE_SUCCESS
    from ghdag.io import done

    _mark_done_file = done.mark_done

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
    return get_config().phase_for_handler(handler)


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
    previous PIPELINE_STATUS. Clear them before ``dag recover``.

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
    must come back; before this they were restored by hand on every recovery.
    """
    from dataclasses import replace

    from issuesmith.ops.labels import project_issue
    from issuesmith.queue import issue_target_meta, resolve_engine

    phase = _phase_for_handler(handler)
    if phase is None:
        print(f"resume: no phase for handler {handler}; labels not restored", file=sys.stderr)
        return
    cfg = get_config()
    try:
        client = get_forge(repo=cfg.repo)
        issue_data = client.issue_get(issue, fields=["state", "labels", "body", "number"])
        add, _remove = project_issue(
            client, issue, lambda st: replace(st, phases={**st.phases, phase: "running"})
        )
        if add:
            print(f"restored label: {', '.join(add)}", file=sys.stderr)
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
    if handler is None:
        table = handler_for_failed_step(from_step, set())
        inferred = _infer_handler_from_exec(issue, from_step)
        if inferred and inferred != table:
            print(f"handler inferred from exec: {inferred} (table said {table})", file=sys.stderr)
        handler = inferred or table
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

    # ghdag recover rejects an unknown step of an existing run, but a handler with no run
    # for the issue resets 0 steps and exits 0. An issue with no exec
    # record at all is left to recover as before: nothing tells the right handler.
    if not steps:
        exec_pairs = _exec_step_handlers(issue)
        if exec_pairs:
            candidates = sorted({h for h, s in exec_pairs if s == from_step} - {handler})
            print(
                f"no steps to reset: step {from_step} not found in handler {handler}"
                f" (try --handler {'/'.join(candidates) or '…'})",
                file=sys.stderr,
            )
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
        name
        for lab in (labels_data.get("labels") or [])
        if isinstance(lab, dict) and isinstance(name := lab.get("name"), str) and name
    }
    apply_redispatch_labels(client, issue, phase, current_labels)
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
