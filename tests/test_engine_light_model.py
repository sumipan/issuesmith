"""light tier のモデル解決が allowlist を尊重し、パイプラインを止めないことの検証。

2026-09-09、codex の light 既定 `gpt-5.4-mini` が nexus の allowlist から外れ、
B1 light tier が `EngineModelError` で停止する事故が #2968 / #2986 と 2 度起きた。
`engine check` は state に light_model が無い（= config 既定を使う）ケースを
検査しておらず、`resolve` も allowlist を見ずにモデルを返していた。
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


@pytest.fixture
def codex_allowlist(monkeypatch):
    """nexus configs/llm-models.yml の codex 実値（2026-09-09）。"""
    allowed = {"gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.5"}

    def _allowed_models(eng: str):
        return set(allowed) if eng == "codex" else None

    monkeypatch.setattr(engine, "_allowed_models", _allowed_models)
    return allowed


def test_light_falls_back_to_heavy_when_light_model_not_allowed(
    monkeypatch, codex_allowlist, capsys
):
    monkeypatch.setattr(engine, "load_state", lambda: _state("gpt-5.4-mini"))
    selection = engine.resolve("design", tier="light")
    assert selection == engine.RoleSelection(engine="codex", model="gpt-5.6-sol")
    err = capsys.readouterr().err
    assert "gpt-5.4-mini" in err and "falling back to gpt-5.6-sol" in err


def test_light_uses_light_model_when_allowed(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state("gpt-5.5"))
    assert engine.resolve("design", tier="light").model == "gpt-5.5"


def test_light_default_falls_back_when_config_default_not_allowed(
    monkeypatch, codex_allowlist
):
    monkeypatch.setattr(engine, "load_state", lambda: _state())
    monkeypatch.setitem(engine.DEFAULT_LIGHT_MODELS, ("design", "codex"), "gpt-5.4-mini")
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
    assert engine.DEFAULT_LIGHT_MODELS[("design", "codex")] in codex_allowlist


def test_light_model_errors_reports_config_default_source(monkeypatch, codex_allowlist):
    monkeypatch.setitem(engine.DEFAULT_LIGHT_MODELS, ("design", "codex"), "gpt-5.4-mini")
    errors = engine.light_model_errors(_state())
    assert len(errors) == 1
    assert "config default" in errors[0]
    assert "gpt-5.4-mini" in errors[0]
    assert "engines.design.light_model.codex" in errors[0]


def test_light_model_errors_reports_state_source(codex_allowlist):
    errors = engine.light_model_errors(_state("gpt-5.4-mini"))
    assert len(errors) == 1 and "(state)" in errors[0]


def test_light_model_errors_empty_when_allowed(codex_allowlist):
    assert engine.light_model_errors(_state("gpt-5.5")) == []
    assert engine.light_model_errors(_state()) == []
