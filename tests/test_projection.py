"""issuesmith.projection: pure state -> label projection and diff (#4807)."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

import issuesmith.projection as projection
from issuesmith.config import IssuesmithConfig, PhaseConfig, get_config
from issuesmith.projection import (
    IssueState,
    diff,
    is_final_step,
    is_managed,
    phase_for_step,
    phase_steps,
    project,
    state_from_labels,
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


def test_project_and_diff_are_pure_and_never_touch_the_forge(cfg, monkeypatch):
    def _boom(*_a, **_k):
        raise AssertionError("forge must not be called")

    monkeypatch.setattr("ghdag.forge.get_forge", _boom)
    monkeypatch.setattr("issuesmith.config.get_config", _boom)
    state = IssueState(phases={"draft": "done", "develop": "running"}, andon_kinds=frozenset({"blocked"}))
    current = [f"{NS}:develop-ready", "scope:milestone"]

    first = project(state, cfg)
    assert project(state, cfg) == first
    assert diff(current, first, cfg) == diff(current, first, cfg)
    assert state.phases == {"draft": "done", "develop": "running"}  # input untouched


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


def test_is_managed(cfg):
    assert is_managed(f"{NS}:draft-done", cfg)
    assert is_managed(f"{NS}:queued", cfg)
    assert is_managed(f"{NS}:waiting", cfg)
    assert is_managed(f"{NS}:andon-blocked", cfg)
    assert not is_managed(f"{NS}:andon-other", cfg)
    assert not is_managed(f"{NS}:migrate-ready", cfg)
    assert not is_managed("other:draft-done", cfg)
    assert not is_managed("scope:milestone", cfg)


def test_state_from_labels_round_trips(cfg):
    labels = [
        f"{NS}:draft-done",
        f"{NS}:develop-ready",
        f"{NS}:develop-running",
        f"{NS}:andon-blocked",
        f"{NS}:queued",
        "scope:milestone",
    ]
    state = state_from_labels(labels, cfg)
    assert state.phases == {"draft": "done", "develop": "running"}
    assert state.andon_kinds == {"blocked"}
    assert state.queued and not state.waiting


def test_steps_resolution():
    phases = (
        PhaseConfig("draft", "design", "b1"),
        PhaseConfig("merge", "implementation", "m2", steps=("m1", "m2-role-dispatch")),
    )
    cfg = replace(get_config(), phases=phases)
    assert phase_steps(phases[0]) == ("b1",)
    assert phase_steps(phases[1]) == ("m1", "m2-role-dispatch")
    assert phase_for_step("b1", cfg) == "draft"
    assert phase_for_step("m1", cfg) == "merge"
    assert phase_for_step("nope", cfg) is None
    assert is_final_step("b1", cfg)
    assert not is_final_step("m1", cfg)
    assert is_final_step("m2-role-dispatch", cfg)
    assert not is_final_step("nope", cfg)


def test_default_config_projects_merge_done_for_m2():
    cfg = get_config()
    assert is_final_step("m2-role-dispatch", cfg)
    phase = phase_for_step("m2-role-dispatch", cfg)
    assert phase is not None
    state = IssueState(phases={phase: "done"})
    assert project(state, cfg) == {f"{cfg.label_namespace}:merge-done"}


def test_projection_module_has_no_phase_or_label_literals(cfg):
    """Phase names and label words come from config / contract, never from string literals."""
    tree = ast.parse(Path(projection.__file__).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    }
    words = {p.name for p in get_config().phases} | {
        "ready", "running", "done", "queued", "waiting", "andon", "andon-",
        "broken", "decision", "blocked", "issuesmith",
    }
    assert not {lit for lit in literals if any(w in lit for w in words)}
