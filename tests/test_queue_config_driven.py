"""tests/test_queue_config_driven.py -- config-driven queue labels and phase functions."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import PhaseConfig, get_config, reset_config_cache
from tests.conftest import NEXUS_TEST_PHASES


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path, monkeypatch):
    reset_config_cache()
    yield
    reset_config_cache()


def _write_cfg(tmp_path, monkeypatch, payload: dict) -> None:
    if "phases" not in payload:
        payload = {**payload, "phases": NEXUS_TEST_PHASES}
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


# ---------------------------------------------------------------------------
# PhaseConfig -- new fields
# ---------------------------------------------------------------------------

def test_phase_config_has_handler_and_preconditions():
    pc = PhaseConfig(
        name="draft",
        role="design",
        entry_step="x",
        handler="brushup",
    )
    assert pc.handler == "brushup"
    assert pc.preconditions == ()


def test_phase_config_with_handler_and_preconditions():
    pc = PhaseConfig(
        name="develop", role="implementation", entry_step="x",
        handler="impl", preconditions=("draft-done",),
    )
    assert pc.handler == "impl"
    assert pc.preconditions == ("draft-done",)


# ---------------------------------------------------------------------------
# queue_triage label dicts -- config-driven
# ---------------------------------------------------------------------------

def test_label_dicts_derived_from_config_namespace(tmp_path, monkeypatch):
    """READY_LABEL etc. use label_namespace from config, not a hardcoded prefix."""
    _write_cfg(tmp_path, monkeypatch, {
        "repo": "test/repo",
        "label_namespace": "testns",
        "phases": [
            {
                "name": "alpha",
                "role": "design",
                "entry_step": "s1",
                "handler": "brushup",
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "beta",
                "role": "implementation",
                "entry_step": "s2",
                "handler": "impl",
                "advance_when": ["deps_terminal"],
            },
        ],
    })
    import issuesmith.queue_triage as qt

    assert qt.READY_LABEL == {"alpha": "testns:alpha-ready", "beta": "testns:beta-ready"}
    assert qt.RUNNING_LABEL == {"alpha": "testns:alpha-running", "beta": "testns:beta-running"}
    assert qt.DONE_LABEL == {"alpha": "testns:alpha-done", "beta": "testns:beta-done"}


def test_terminal_without_merge_uses_config(tmp_path, monkeypatch):
    """get_terminal_without_merge includes declared terminal_without_merge labels."""
    _write_cfg(tmp_path, monkeypatch, {
        "repo": "test/repo",
        "label_namespace": "testns",
        "terminal_without_merge": ["rejected", "superseded"],
        "terminal_labels": ["testns:merge-done", "bump:done"],
    })
    from importlib import reload

    import issuesmith.queue_triage as qt
    reload(qt)
    try:
        twm = qt.get_terminal_without_merge()
        assert "testns:rejected" in twm
        assert "testns:superseded" in twm
        assert "bump:done" in twm
        assert "testns:merge-done" not in twm
    finally:
        reload(qt)


# ---------------------------------------------------------------------------
# _build_phases -- handler and preconditions parsed from YAML
# ---------------------------------------------------------------------------

def test_build_phases_parses_handler_and_preconditions(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {
        "repo": "test/repo",
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "s0",
                "handler": "brushup",
                "preconditions": [],
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "s1",
                "handler": "impl",
                "preconditions": ["draft-done"],
                "advance_when": ["deps_terminal"],
            },
        ],
    })
    cfg = get_config()
    draft = next(p for p in cfg.phases if p.name == "draft")
    develop = next(p for p in cfg.phases if p.name == "develop")
    assert draft.handler == "brushup"
    assert draft.preconditions == ()
    assert develop.handler == "impl"
    assert develop.preconditions == ("draft-done",)


# ---------------------------------------------------------------------------
# handler_for_failed_step -- no hardcoded step names
# ---------------------------------------------------------------------------

def test_handler_for_failed_step_falls_back_to_impl(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {"repo": "test/repo"})
    import issuesmith.queue as qmod
    labels: set[str] = set()
    result = qmod.handler_for_failed_step("unknown-step", labels)
    assert result == "impl"


def test_handler_for_failed_step_uses_workflow_yaml(tmp_path, monkeypatch):
    """When workflow YAML is present, step-to-handler map is read from it."""
    wf_content = {
        "label_namespace": "issuesmith",
        "handlers": {
            "brushup": {"steps": [{"id": "design-entry", "template": "t.md"}]},
            "impl": {"steps": [{"id": "impl-entry", "template": "t.md"}]},
        },
    }
    wf_path = tmp_path / "issuesmith.yml"
    wf_path.write_text(yaml.safe_dump(wf_content), encoding="utf-8")
    cfg_content = {
        "repo": "test/repo",
        "paths": {"workflow": str(wf_path)},
    }
    _write_cfg(tmp_path, monkeypatch, cfg_content)
    import issuesmith.queue as qmod
    assert qmod.handler_for_failed_step("design-entry", set()) == "brushup"
    assert qmod.handler_for_failed_step("impl-entry", set()) == "impl"


# ---------------------------------------------------------------------------
# phase_preconditions -- declaration-driven
# ---------------------------------------------------------------------------

def test_phase_preconditions_draft_ok_when_no_labels(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {"repo": "test/repo"})
    from unittest.mock import MagicMock

    import issuesmith.queue as qmod
    client = MagicMock()
    client.api_request.return_value = []
    issue = {"state": "open", "labels": [], "body": ""}
    ok, _ = qmod.phase_preconditions("draft", issue, client, 1)
    assert ok


def test_phase_preconditions_develop_blocked_without_design_done(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {"repo": "test/repo"})
    from unittest.mock import MagicMock

    import issuesmith.queue as qmod
    client = MagicMock()
    issue = {"state": "open", "labels": [], "body": ""}
    ok, reason = qmod.phase_preconditions("develop", issue, client, 1)
    assert not ok
    assert "required" in reason


# ---------------------------------------------------------------------------
# ops/labels._MANAGED_PHASES -- config-driven
# ---------------------------------------------------------------------------

def test_managed_phases_from_config(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {
        "repo": "test/repo",
        "phases": [
            {
                "name": "alpha",
                "role": "design",
                "entry_step": "s1",
                "handler": "brushup",
                "advance_when": ["deps_terminal"],
            },
        ],
    })
    from importlib import reload

    import issuesmith.ops.labels as lmod
    reload(lmod)
    try:
        assert "alpha" in lmod._MANAGED_PHASES
        assert "draft" not in lmod._MANAGED_PHASES
    finally:
        reload(lmod)
