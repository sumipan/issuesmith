"""Verify light-tier model resolution respects the allowlist and does not halt the pipeline.

On 2026-09-09, codex's light default `gpt-5.4-mini` fell outside nexus's allowlist and
B1 light tier stopped with `EngineModelError` twice (#2968 / #2986).
`engine check` did not inspect the case where state has no light_model (= uses the
config default), and `resolve` returned the model without consulting the allowlist.
"""
from __future__ import annotations

import pytest

from issuesmith import engine


def _state(light_model: str | None = None) -> dict[str, dict[str, str]]:
    design: dict[str, str] = {"engine": "codex", "model": "gpt-5.6-sol"}
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
        return set(allowed) if eng == "codex" else None

    monkeypatch.setattr(engine, "_allowed_models", _allowed_models)
    return allowed


def test_light_uses_light_model_when_allowed(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state("gpt-5.5"))
    assert engine.resolve("design", tier="light").model == "gpt-5.5"


def test_light_default_falls_back_when_config_default_not_allowed(
    monkeypatch, codex_allowlist
):
    monkeypatch.setattr(engine, "load_state", lambda: _state())
    _patch_design_codex_light(monkeypatch, "gpt-5.4-mini")
    assert engine.resolve("design", tier="light").model == "gpt-5.6-sol"


def test_light_skips_validation_when_allowlist_unreadable(monkeypatch):
    monkeypatch.setattr(engine, "_allowed_models", lambda eng: None)
    monkeypatch.setattr(engine, "load_state", lambda: _state("anything-goes"))
    assert engine.resolve("design", tier="light").model == "anything-goes"


def test_heavy_tier_ignores_light_model(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state("gpt-5.4-mini"))
    assert engine.resolve("design").model == "gpt-5.6-sol"
    assert engine.resolve("design", tier="heavy").model == "gpt-5.6-sol"


def test_builtin_codex_light_default_is_in_current_allowlist(codex_allowlist):
    light = engine.LIGHT_ROUTES["design"]
    assert light is not None
    codex_model = next(model for eng, model in light if eng == "codex")
    assert codex_model in codex_allowlist


def test_light_model_errors_reports_config_default_source(monkeypatch, codex_allowlist):
    _patch_design_codex_light(monkeypatch, "gpt-5.4-mini")
    errors = engine.light_model_errors(_state())
    assert len(errors) == 1
    assert "config default" in errors[0]
    assert "gpt-5.4-mini" in errors[0]
    assert "engines.design.light (codex)" in errors[0]


def test_light_model_errors_reports_state_source(codex_allowlist):
    errors = engine.light_model_errors(_state("gpt-5.4-mini"))
    assert len(errors) == 1 and "(state)" in errors[0]


def test_light_model_errors_empty_when_allowed(codex_allowlist):
    assert engine.light_model_errors(_state("gpt-5.5")) == []
    assert engine.light_model_errors(_state()) == []
