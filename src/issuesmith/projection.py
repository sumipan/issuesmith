"""Pure label projection: issue state -> managed label set (#4807).

Nothing here touches the forge or ``get_config()``; the configuration is passed in.
Phase names come from ``config.phases``, the namespace from ``config.label_namespace``
and the label vocabulary from :mod:`issuesmith.contract`. The only writer that applies
a projection to an Issue is ``ops/labels.project_issue``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable, Mapping

from issuesmith.contract import ANDON_KINDS, ANDON_PREFIX, PHASE_STATUSES, QUEUED, WAITING

if TYPE_CHECKING:
    from issuesmith.config import IssuesmithConfig, PhaseConfig

# Statuses past ``ready``: a phase in one of them keeps its precondition labels.
_ACTIVE_STATUSES = PHASE_STATUSES[1:]


@dataclass(frozen=True)
class IssueState:
    phases: Mapping[str, str] = field(default_factory=dict)  # phase name -> PHASE_STATUSES item
    andon_kinds: frozenset[str] = frozenset()
    queued: bool = False
    waiting: bool = False


def phase_steps(phase: PhaseConfig) -> tuple[str, ...]:
    """Steps of ``phase`` in run order; ``(entry_step,)`` when none are declared."""
    return phase.steps or (phase.entry_step,)


def _phase_of(step_id: str, config: IssuesmithConfig) -> PhaseConfig | None:
    return next((p for p in config.phases if step_id in phase_steps(p)), None)


def phase_for_step(step_id: str, config: IssuesmithConfig) -> str | None:
    phase = _phase_of(step_id, config)
    return phase.name if phase is not None else None


def is_final_step(step_id: str, config: IssuesmithConfig) -> bool:
    phase = _phase_of(step_id, config)
    return phase is not None and phase_steps(phase)[-1] == step_id


def _label(config: IssuesmithConfig, suffix: str) -> str:
    return f"{config.label_namespace}:{suffix}"


def _phase_label(config: IssuesmithConfig, phase: str, status: str) -> str:
    return _label(config, f"{phase}-{status}")


def _andon_label(config: IssuesmithConfig, kind: str) -> str:
    return _label(config, f"{ANDON_PREFIX}{kind}")


def is_managed(label: str, config: IssuesmithConfig) -> bool:
    """True if ``label`` belongs to the phase / attention axes or the queued / waiting markers."""
    prefix = f"{config.label_namespace}:"
    if not label.startswith(prefix):
        return False
    suffix = label[len(prefix):]
    if suffix in (QUEUED, WAITING):
        return True
    if suffix.startswith(ANDON_PREFIX) and suffix[len(ANDON_PREFIX):] in ANDON_KINDS:
        return True
    return any(
        suffix == f"{phase.name}-{status}" for phase in config.phases for status in PHASE_STATUSES
    )


def state_from_labels(labels: Iterable[str], config: IssuesmithConfig) -> IssueState:
    """Read the state the current labels express (the most advanced status per phase)."""
    present = set(labels)
    phases: dict[str, str] = {}
    for phase in config.phases:
        for status in PHASE_STATUSES:
            if _phase_label(config, phase.name, status) in present:
                phases[phase.name] = status
    return IssueState(
        phases=phases,
        andon_kinds=frozenset(k for k in ANDON_KINDS if _andon_label(config, k) in present),
        queued=_label(config, QUEUED) in present,
        waiting=_label(config, WAITING) in present,
    )


def project(state: IssueState, config: IssuesmithConfig) -> frozenset[str]:
    """Desired managed labels for ``state``.

    One phase-axis label (later declared phase wins, then the more advanced status),
    the precondition labels of running / done phases, one attention label (most urgent
    andon kind), and the additive queued / waiting markers.
    """
    order = {p.name: i for i, p in enumerate(config.phases)}
    by_name = {p.name: p for p in config.phases}
    known = [
        (name, status)
        for name, status in state.phases.items()
        if name in order and status in PHASE_STATUSES
    ]
    labels: set[str] = set()
    if known:
        name, status = max(known, key=lambda ns: (order[ns[0]], PHASE_STATUSES.index(ns[1])))
        labels.add(_phase_label(config, name, status))
    for name, status in known:
        if status not in _ACTIVE_STATUSES:
            continue
        for precond in by_name[name].preconditions:
            labels.add(precond if ":" in precond else _label(config, precond))
    kind = next((k for k in ANDON_KINDS if k in state.andon_kinds), None)
    if kind is not None:
        labels.add(_andon_label(config, kind))
    if state.queued:
        labels.add(_label(config, QUEUED))
    if state.waiting:
        labels.add(_label(config, WAITING))
    return frozenset(labels)


def diff(
    current: Iterable[str], desired: frozenset[str], config: IssuesmithConfig
) -> tuple[list[str], list[str]]:
    """Return sorted ``(add, remove)`` over managed labels; unmanaged labels are never removed."""
    managed_current = {lb for lb in current if is_managed(lb, config)}
    managed_desired = {lb for lb in desired if is_managed(lb, config)}
    return sorted(managed_desired - managed_current), sorted(managed_current - managed_desired)
