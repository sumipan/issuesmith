"""tests/test_queue_config_driven.py -- config-driven queue labels and phase functions."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import PhaseConfig, get_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_cache(tmp_path, monkeypatch):
    reset_config_cache()
    yield
    reset_config_cache()


def _write_cfg(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


# ---------------------------------------------------------------------------
# PhaseConfig -- new fields
# ---------------------------------------------------------------------------

def test_phase_config_has_handler_and_preconditions():
    pc = PhaseConfig(name="draft", role="design", entry_step="x")
    assert pc.handler == ""
    assert pc.preconditions == ()


def test_phase_config_with_handler_and_preconditions():
    pc = PhaseConfig(
        name="develop", role="implementation", entry_step="x",
        handler="impl", preconditions=("draft",),
    )
    assert pc.handler == "impl"
    assert pc.preconditions == ("draft",)


# ---------------------------------------------------------------------------
# queue_triage label dicts -- config-driven
# ---------------------------------------------------------------------------

def test_label_dicts_derived_from_config_namespace(tmp_path, monkeypatch):
    """READY_LABEL etc. use label_namespace from config, not a hardcoded prefix."""
    _write_cfg(tmp_path, monkeypatch, {
        "repo": "test/repo",
        "label_namespace": "testns",
        "phases": [
            {"name": "alpha", "role": "design", "entry_step": "s1"},
            {"name": "beta", "role": "implementation", "entry_step": "s2"},
        ],
    })
    from importlib import reload

    import issuesmith.queue_triage as qt
    reload(qt)
    try:
        assert qt.READY_LABEL == {"alpha": "testns:alpha-ready", "beta": "testns:beta-ready"}
        assert qt.RUNNING_LABEL == {"alpha": "testns:alpha-running", "beta": "testns:beta-running"}
        assert qt.DONE_LABEL == {"alpha": "testns:alpha-done", "beta": "testns:beta-done"}
    finally:
        reload(qt)  # restore defaults for other tests


def test_terminal_without_merge_uses_config(tmp_path, monkeypatch):
    """TERMINAL_WITHOUT_MERGE includes sub-phase labels from config."""
    _write_cfg(tmp_path, monkeypatch, {"repo": "test/repo"})
    from importlib import reload

    import issuesmith.queue_triage as qt
    reload(qt)
    try:
        twm = qt.TERMINAL_WITHOUT_MERGE
        ns = get_config().label_namespace
        assert f"{ns}:rejected" in twm
        assert f"{ns}:superseded" in twm
    finally:
        reload(qt)


# ---------------------------------------------------------------------------
# _build_phases -- handler and preconditions parsed from YAML
# ---------------------------------------------------------------------------

def test_build_phases_parses_handler_and_preconditions(tmp_path, monkeypatch):
    _write_cfg(tmp_path, monkeypatch, {
        "repo": "test/repo",
        "phases": [
            {"name": "draft", "role": "design", "entry_step": "s0", "handler": "brushup", "preconditions": []},
            {"name": "develop", "role": "implementation", "entry_step": "s1", "handler": "impl", "preconditions": ["draft"]},
        ],
    })
    cfg = get_config()
    draft = next(p for p in cfg.phases if p.name == "draft")
    develop = next(p for p in cfg.phases if p.name == "develop")
    assert draft.handler == "brushup"
    assert draft.preconditions == ()
    assert develop.handler == "impl"
    assert develop.preconditions == ("draft",)


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
# _phase_preconditions -- no banned literal label strings in source
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
            {"name": "alpha", "role": "design", "entry_step": "s1"},
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
