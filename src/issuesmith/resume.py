"""Central resume entry point (#3509).

resume(issue, *, from_step=None, phase=None) unifies recover/redispatch:
  - from_step: ghdag dag recover + in_flight release
  - phase: in_flight release + label reset + queue re-enqueue
"""
from __future__ import annotations

import json
import sys

from ghdag.forge import get_forge

from issuesmith.config import get_config
from issuesmith.queue import apply_redispatch_labels, handler_for_failed_step
from issuesmith.queue_store import QueueStore


def resume(issue: int, *, from_step: str | None = None, phase: str | None = None) -> int:
    """Resume a stalled issue.

    from_step: resume from a specific DAG step (ghdag dag recover equivalent)
    phase: redispatch to phase (label reset + queue re-enqueue)

    Always releases in_flight before acting.
    """
    if from_step is None and phase is None:
        raise ValueError("resume() requires from_step= or phase=")

    QueueStore().remove_in_flight(issue)

    if from_step is not None:
        return _resume_from_step(issue, from_step)

    assert phase is not None
    return _resume_phase(issue, phase)


def _generation_keys_available() -> bool:
    from issuesmith.recovery import _generation_keys_available as _gka
    return _gka()


def _run_ghdag_recover(issue: int, handler: str, from_step: str | None) -> int:
    from issuesmith.recovery import _run_ghdag_recover as _rgr
    return _rgr(issue, handler, from_step)


def _resume_from_step(issue: int, from_step: str) -> int:
    handler = handler_for_failed_step(from_step, set())
    if not _generation_keys_available():
        print(
            f"ghdag dag recover is not available; cannot resume from step {from_step!r}",
            file=sys.stderr,
        )
        return 1
    return _run_ghdag_recover(issue, handler, from_step)


def _resume_phase(issue: int, phase: str) -> int:
    client = get_forge(repo=get_config().repo)
    labels_data = client.issue_get(issue, fields=["labels"])
    current_labels = {
        lab.get("name")
        for lab in (labels_data.get("labels") or [])
        if isinstance(lab, dict) and lab.get("name")
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
