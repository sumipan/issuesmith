"""Light-route allowlist checks and resolve --tier light allowlist_valid.

#2981: engine check inspects every light-route pair and,
if out of allowlist, points at the issuesmith.yaml key to fix. resolve --tier light
emits allowlist_valid.
"""
from __future__ import annotations

import pytest

from issuesmith import engine


def _state(
    *,
    design_engine: str = "codex",
    design_model: str = "gpt-5.6-sol",
    light_model: str | None = None,
) -> dict[str, dict[str, str]]:
    design: dict[str, str] = {"engine": design_engine, "model": design_model}
    if light_model:
        design["light_model"] = light_model
    return {
        "design": design,
        "implementation": {"engine": "cursor", "model": "auto"},
    }


def _patch_design_codex_light(monkeypatch, model: str) -> None:
    light = engine.LIGHT_ROUTES["design"]
    assert light is not None
    patched = tuple((eng, model if eng == "codex" else existing) for eng, existing in light)
    monkeypatch.setitem(engine.LIGHT_ROUTES, "design", patched)


@pytest.fixture
def codex_allowlist(monkeypatch):
    """codex values from nexus configs/llm-models.yml (2026-09-09)."""
    allowed = {"gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.5"}

    def _allowed_models(eng: str):
        return set(allowed) if eng == "codex" else frozenset({"claude-sonnet-4-6", "claude-opus-4-6"})

    monkeypatch.setattr(engine, "_allowed_models", _allowed_models)
    return allowed


def test_state_light_model_override_still_checked(codex_allowlist):
    errors = engine.light_model_errors(_state(light_model="gpt-5.4-mini"))
    assert any("(state)" in e and "gpt-5.4-mini" in e for e in errors)


def test_state_override_unchanged_when_allowed(codex_allowlist):
    assert engine.light_model_errors(_state(light_model="gpt-5.5")) == []


def test_resolve_light_falls_back_before_llm_call(monkeypatch, codex_allowlist, capsys):
    monkeypatch.setattr(engine, "load_state", lambda: _state(light_model="gpt-5.4-mini"))
    selection = engine.resolve("design", tier="light")
    assert selection.model == "gpt-5.6-sol"
    assert "falling back" in capsys.readouterr().err


def test_light_allowlist_status_invalid(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state(light_model="gpt-5.4-mini"))
    valid, reason = engine.light_allowlist_status("design")
    assert valid is False
    assert reason is not None
    assert "gpt-5.4-mini" in reason


def test_light_allowlist_status_valid(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state(light_model="gpt-5.5"))
    valid, reason = engine.light_allowlist_status("design")
    assert valid is True
    assert reason is None


def test_resolve_cli_prints_allowlist_valid_true(monkeypatch, codex_allowlist, capsys):
    monkeypatch.setattr(engine, "load_state", lambda: _state(light_model="gpt-5.5"))
    assert engine.main(["resolve", "design", "--tier", "light"]) == 0
    out = capsys.readouterr().out
    assert "allowlist_valid: true" in out
    assert "gpt-5.5" in out


def test_resolve_cli_prints_allowlist_valid_false_with_reason(
    monkeypatch, codex_allowlist, capsys
):
    monkeypatch.setattr(engine, "load_state", lambda: _state(light_model="gpt-5.4-mini"))
    assert engine.main(["resolve", "design", "--tier", "light"]) == 0
    out = capsys.readouterr().out
    assert "allowlist_valid: false" in out
    assert "reason:" in out
    assert "gpt-5.4-mini" in out


def test_check_state_workflow_drift_for_module_step(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state())
    monkeypatch.setattr(
        engine, "resolve", lambda role, tier=None: engine.RoleSelection("codex", "gpt-5.6-sol")
    )
    monkeypatch.setattr(engine.shutil, "which", lambda cmd: "/bin/true")
    monkeypatch.setattr(engine, "_module_declared_step_ids", lambda: frozenset({"implement"}))
    monkeypatch.setattr(
        engine,
        "_iter_steps",
        lambda wf: [
            ("impl", {"id": "implement", "engine": "claude", "model": "opus"}),
        ],
    )
    monkeypatch.setattr(engine, "_load_workflow", lambda: {})
    errors = engine.check_state()
    assert any("workflow drift" in e and "implement" in e for e in errors)


def test_check_state_skips_steps_without_module(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state())
    monkeypatch.setattr(
        engine, "resolve", lambda role, tier=None: engine.RoleSelection("codex", "gpt-5.6-sol")
    )
    monkeypatch.setattr(engine.shutil, "which", lambda cmd: "/bin/true")
    monkeypatch.setattr(engine, "_module_declared_step_ids", lambda: frozenset({"implement"}))
    monkeypatch.setattr(
        engine,
        "_iter_steps",
        lambda wf: [
            ("impl", {"id": "design", "engine": "claude", "model": "opus"}),
            ("impl", {"id": "implement", "engine": "shell", "model": "bash"}),
        ],
    )
    monkeypatch.setattr(engine, "_load_workflow", lambda: {})
    assert not any("design" in e for e in engine.check_state())


