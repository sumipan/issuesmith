"""DEFAULT_LIGHT_MODELS の許可リスト突合と resolve --tier light の allowlist_valid。

#2981: engine check は config 既定 light の全 (role, engine) を検査し、
外れていれば issuesmith.yaml の修正キーを案内する。resolve --tier light は
allowlist_valid を出力する。
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


@pytest.fixture
def codex_allowlist(monkeypatch):
    """nexus configs/llm-models.yml の codex 実値（2026-09-09）。"""
    allowed = {"gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.5"}

    def _allowed_models(eng: str):
        return set(allowed) if eng == "codex" else frozenset({"claude-sonnet-4-6", "claude-opus-4-6"})

    monkeypatch.setattr(engine, "_allowed_models", _allowed_models)
    return allowed


def test_default_light_models_all_entries_checked_even_if_not_current_engine(
    monkeypatch, codex_allowlist
):
    """現在 engine が claude でも、codex の config 既定が外れていれば NG。"""
    monkeypatch.setattr(engine, "load_state", lambda: _state(design_engine="claude", design_model="claude-opus-4-6"))
    monkeypatch.setitem(engine.DEFAULT_LIGHT_MODELS, ("design", "codex"), "gpt-5.4-mini")
    errors = engine.light_model_errors()
    assert any("engines.design.light_model.codex" in e for e in errors)
    assert any("gpt-5.4-mini" in e for e in errors)


def test_default_light_models_error_guides_issuesmith_yaml_key(
    monkeypatch, codex_allowlist
):
    monkeypatch.setitem(engine.DEFAULT_LIGHT_MODELS, ("design", "codex"), "gpt-5.4-mini")
    errors = engine.light_model_errors(_state())
    assert len(errors) >= 1
    joined = "\n".join(errors)
    assert "engines.design.light_model.codex" in joined
    assert "issuesmith.yaml" in joined or "DEFAULT_LIGHT_MODELS" in joined


def test_state_light_model_override_still_checked(codex_allowlist):
    errors = engine.light_model_errors(_state(light_model="gpt-5.4-mini"))
    assert any("(state)" in e and "gpt-5.4-mini" in e for e in errors)


def test_state_override_unchanged_when_allowed(codex_allowlist):
    assert engine.light_model_errors(_state(light_model="gpt-5.5")) == []


def test_check_state_includes_default_light_allowlist_errors(monkeypatch, codex_allowlist):
    monkeypatch.setattr(engine, "load_state", lambda: _state())
    monkeypatch.setitem(engine.DEFAULT_LIGHT_MODELS, ("design", "codex"), "gpt-5.4-mini")
    monkeypatch.setattr(engine, "resolve", lambda role, tier=None: engine.RoleSelection("codex", "gpt-5.6-sol"))
    monkeypatch.setattr(engine.shutil, "which", lambda cmd: "/bin/true")
    monkeypatch.setattr(engine, "_load_workflow", lambda: {"handlers": {}})
    monkeypatch.setattr(engine, "_iter_steps", lambda wf: [])
    # IMPLEMENTATION_STEP_IDS 欠落エラーを避けるため空集合に
    monkeypatch.setattr(engine, "IMPLEMENTATION_STEP_IDS", frozenset())
    errors = engine.check_state()
    assert any("engines.design.light_model.codex" in e for e in errors)


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


def test_execute_engine_override_falls_back_when_light_not_allowed(
    monkeypatch, codex_allowlist, capsys
):
    """--engine 上書き経路でも allowlist 外 light は heavy に戻す。"""
    monkeypatch.setitem(engine.DEFAULT_LIGHT_MODELS, ("design", "codex"), "gpt-5.4-mini")
    monkeypatch.setitem(engine.DEFAULT_MODELS, ("design", "codex"), "gpt-5.6-sol")
    model = engine._apply_light_or_fallback(
        role="design",
        engine="codex",
        heavy_model="gpt-5.6-sol",
        light_model="gpt-5.4-mini",
        source="config default",
    )
    assert model == "gpt-5.6-sol"
    err = capsys.readouterr().err
    assert "gpt-5.4-mini" in err and "falling back to gpt-5.6-sol" in err
