"""Requires evaluation and repair orchestration for completed steps (#3626).

When dispatch detects that a step completed with ``status == "done"``, this
module evaluates all gates declared in ``StepConfig.requires`` and:

  1. Checks each gate; classifies violations as preexisting (on base branch) vs
     blocking (new in this branch).
  2. Applies deterministic auto-fixes via ``Gate.fix()`` for auto_fixable
     violations (fix must only narrow, never widen).
  3. Returns a ``RequiresResult`` that dispatch uses to decide whether to
     proceed (emit markers), launch a repair step (LLM), or raise andon.

Repair-step launch and max_repairs bookkeeping live in ``ops/dispatch.py``
because they need the same raise_andon / RetrySignal machinery.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RequiresResult:
    """Result of one requires evaluation pass."""

    blocking: list = field(default_factory=list)    # violations that block progress
    preexisting: list = field(default_factory=list) # same violations on base branch
    auto_fixed: list = field(default_factory=list)  # rule_ids auto-fixed this pass
    gate_error: Exception | None = None             # set if any gate raised


def evaluate_requires(
    gates: dict[str, Any],
    body: str,
    labels: list[str],
    *,
    preexisting_rule_ids: frozenset[str] = frozenset(),
) -> RequiresResult:
    """Call check() on each gate; classify results into blocking / preexisting.

    If any gate raises an exception the result has gate_error set and no
    violations — dispatch should treat this as andon(broken).
    """
    all_violations: list = []
    for gate in gates.values():
        try:
            vs = gate.check(body, labels)
            all_violations.extend(vs)
        except Exception as exc:
            return RequiresResult(gate_error=exc)

    preexisting = [v for v in all_violations if v.rule_id in preexisting_rule_ids]
    blocking = [v for v in all_violations if v.rule_id not in preexisting_rule_ids]
    return RequiresResult(blocking=blocking, preexisting=preexisting)


def apply_auto_fixes(
    result: RequiresResult,
    gates: dict[str, Any],
    inp: Any,
) -> tuple[RequiresResult, Any]:
    """Call gate.fix() for each auto_fixable blocking violation.

    Returns (updated RequiresResult, updated contract_input).  Violations whose
    gate has no fix() or whose fix() raises are kept in blocking.
    """
    still_blocking = []
    auto_fixed = list(result.auto_fixed)

    for v in result.blocking:
        if not v.auto_fixable:
            still_blocking.append(v)
            continue
        gate_id = (v.rule_id or "").split(".")[0]
        gate = gates.get(gate_id)
        if gate is not None and hasattr(gate, "fix"):
            try:
                inp = gate.fix(inp)
                auto_fixed.append(v.rule_id)
                continue
            except Exception:
                pass
        still_blocking.append(v)

    updated = RequiresResult(
        blocking=still_blocking,
        preexisting=result.preexisting,
        auto_fixed=auto_fixed,
    )
    return updated, inp


def record_metrics(path: Path, event: str, step_id: str, issue_num: int) -> None:
    """Append a requires_check or requires_repair event to the metrics file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps({"event": event, "step": step_id, "issue": issue_num}) + "\n"
            )
    except OSError:
        pass


__all__ = ["RequiresResult", "evaluate_requires", "apply_auto_fixes", "record_metrics"]
