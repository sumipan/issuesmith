"""``engine show --json`` output (#6780 / parent #6701)."""

from __future__ import annotations

import json

from issuesmith import engine

_WSL_HEAVY_ROUTES = {
    "implementation": (
        ("claude", "claude-sonnet-4-6"),
        ("cursor", "auto"),
        ("codex", "gpt-5.6-sol"),
    ),
    "review": (
        ("claude", "claude-opus-4-6"),
        ("cursor", "auto"),
        ("codex", "gpt-5.6-sol"),
    ),
}
_WSL_ROLE_ENGINES = {
    role: tuple(engine_name for engine_name, _ in routes)
    for role, routes in _WSL_HEAVY_ROUTES.items()
}
_WSL_LIGHT_ROUTES = {role: None for role in _WSL_HEAVY_ROUTES}


def test_engine_show_json(monkeypatch, capsys) -> None:
    """refs #6780: engine show --json exposes state_source and per-role routes."""
    monkeypatch.setattr(engine, "ROLE_ENGINES", _WSL_ROLE_ENGINES)
    monkeypatch.setattr(engine, "HEAVY_ROUTES", _WSL_HEAVY_ROUTES)
    monkeypatch.setattr(engine, "LIGHT_ROUTES", _WSL_LIGHT_ROUTES)
    monkeypatch.setattr(engine, "load_state", engine._default_state)
    monkeypatch.setattr(engine, "state_source_label", lambda: "default")

    code = engine.main(["show", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state_source"] == "default"
    roles = payload["roles"]
    assert isinstance(roles, list)
    assert {row["role"] for row in roles} == {"implementation", "review"}
    for row in roles:
        assert set(row) == {"role", "engine", "model"}
