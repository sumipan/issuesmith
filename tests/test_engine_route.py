"""Tests for engine route construction (sumipan/nexus#6681, #6682)."""

from __future__ import annotations

from issuesmith.engine import resolve
from issuesmith.engine_route import build_route


def test_light_tier_reorders_selected_within_light_route():
    """Refs sumipan/nexus#6682 AC1 — selected engine leads within the light route."""
    heavy = [("claude", "h-c"), ("cursor", "h-x")]
    light = [("cursor", "l-x"), ("claude", "l-c")]

    assert build_route(
        heavy=heavy,
        light=light,
        selected_engine="cursor",
        selected_model="ignored",
        selected_light_model=None,
        tier="light",
    ) == [("cursor", "l-x"), ("claude", "l-c")]

    assert build_route(
        heavy=heavy,
        light=light,
        selected_engine="claude",
        selected_model="ignored",
        selected_light_model=None,
        tier="light",
    ) == [("claude", "l-c"), ("cursor", "l-x")]


def test_light_tier_without_light_route_matches_heavy():
    """Refs sumipan/nexus#6682 AC2 — missing light route falls back to heavy ordering."""
    heavy = [("claude", "a"), ("cursor", "b")]
    heavy_route = build_route(
        heavy=heavy,
        light=None,
        selected_engine="claude",
        selected_model="m-heavy",
        selected_light_model=None,
        tier="heavy",
    )
    light_route = build_route(
        heavy=heavy,
        light=None,
        selected_engine="claude",
        selected_model="m-heavy",
        selected_light_model=None,
        tier="light",
    )
    assert light_route == heavy_route


def test_state_light_model_not_applied_to_other_engine(monkeypatch):
    """Refs sumipan/nexus#6682 — state light_model is only used for the state engine."""
    monkeypatch.setattr(
        "issuesmith.engine.load_state",
        lambda: {
            "design": {"engine": "claude", "model": "heavy-m", "light_model": "claude-only"},
        },
    )
    monkeypatch.setattr(
        "issuesmith.engine.HEAVY_ROUTES",
        {"design": (("claude", "heavy-m"), ("codex", "hx"))},
    )
    monkeypatch.setattr(
        "issuesmith.engine.LIGHT_ROUTES",
        {"design": (("codex", "lx"),)},
    )
    monkeypatch.setattr("issuesmith.engine._light_model_allowed", lambda _e, _m: True)

    head = resolve("design", "light")
    assert (head.engine, head.model) == ("codex", "lx")
    from issuesmith.engine import RoleSelection, _build_role_route

    assert _build_role_route("design", RoleSelection("codex", "hx"), "light", ()) == [
        ("codex", "lx")
    ]
