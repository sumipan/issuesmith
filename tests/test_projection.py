"""issuesmith.projection: pure state -> label projection and diff (#4807)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from issuesmith.config import IssuesmithConfig, get_config
from issuesmith.projection import (
    IssueState,
    diff,
    is_final_step,
    phase_for_step,
    project,
)

NS = "ns"


@pytest.fixture
def cfg() -> IssuesmithConfig:
    """Default phases with develop requiring draft-done, under namespace ``ns``."""
    base = get_config()
    phases = tuple(
        replace(p, preconditions=("draft-done",)) if p.name == "develop" else p
        for p in base.phases
    )
    return replace(base, label_namespace=NS, phases=phases)


def test_phase_axis_keeps_latest_phase_and_its_preconditions(cfg):
    state = IssueState(phases={"draft": "done", "develop": "running"})
    assert project(state, cfg) == {f"{NS}:develop-running", f"{NS}:draft-done"}


def test_phase_axis_without_preconditions_is_a_single_label(cfg):
    cfg = replace(cfg, phases=tuple(replace(p, preconditions=()) for p in cfg.phases))
    state = IssueState(phases={"draft": "done", "develop": "running"})
    assert project(state, cfg) == {f"{NS}:develop-running"}


def test_ready_phase_does_not_keep_preconditions(cfg):
    state = IssueState(phases={"draft": "done", "develop": "ready"})
    assert project(state, cfg) == {f"{NS}:develop-ready"}


def test_declaration_order_wins_over_status(cfg):
    state = IssueState(phases={"draft": "done", "merge": "ready"})
    assert project(state, cfg) == {f"{NS}:merge-ready"}


def test_unknown_phase_or_status_is_ignored(cfg):
    state = IssueState(phases={"migrate": "done", "draft": "bogus"})
    assert project(state, cfg) == frozenset()


def test_attention_axis_is_the_most_urgent_kind(cfg):
    state = IssueState(andon_kinds=frozenset({"blocked", "broken"}))
    assert project(state, cfg) == {f"{NS}:andon-broken"}
    state = IssueState(andon_kinds=frozenset({"blocked", "decision"}))
    assert project(state, cfg) == {f"{NS}:andon-decision"}


def test_queued_and_waiting_are_additive(cfg):
    state = IssueState(phases={"draft": "done"}, queued=True, waiting=True)
    assert project(state, cfg) == {f"{NS}:draft-done", f"{NS}:queued", f"{NS}:waiting"}


def test_diff_never_removes_unmanaged_labels(cfg):
    current = ["scope:milestone", "bug", f"{NS}:develop-ready", f"{NS}:other"]
    desired = project(IssueState(phases={"develop": "running"}), cfg)
    assert diff(current, desired, cfg) == (
        [f"{NS}:develop-running", f"{NS}:draft-done"],
        [f"{NS}:develop-ready"],
    )


def test_diff_of_equal_sets_is_empty(cfg):
    desired = project(IssueState(phases={"merge": "done"}, andon_kinds=frozenset({"decision"})), cfg)
    assert diff(set(desired) | {"scope:milestone"}, desired, cfg) == ([], [])


def test_diff_lists_are_sorted(cfg):
    current = [f"{NS}:waiting", f"{NS}:andon-blocked", f"{NS}:draft-ready"]
    desired = frozenset({f"{NS}:queued", f"{NS}:merge-done", f"{NS}:andon-broken"})
    add, remove = diff(current, desired, cfg)
    assert add == sorted(add) and remove == sorted(remove)
    assert set(remove) == set(current)


def test_default_config_projects_merge_done_for_m2():
    cfg = get_config()
    assert is_final_step("m2", cfg)
    phase = phase_for_step("m2", cfg)
    assert phase is not None
    state = IssueState(phases={phase: "done"})
    assert project(state, cfg) == {f"{cfg.label_namespace}:merge-done"}
