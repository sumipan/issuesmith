"""Tests for engine route construction (sumipan/nexus#6681, #6682)."""

from __future__ import annotations

from issuesmith.engine import resolve, route
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


def test_heavy_tier_puts_selected_first_with_model():
    """Refs sumipan/nexus#6682 AC3 — selected engine and model lead on heavy tier."""
    result = build_route(
        heavy=[("claude", "a"), ("codex", "b"), ("cursor", "c")],
        light=None,
        selected_engine="codex",
        selected_model="b2",
        selected_light_model=None,
        tier="heavy",
    )
    assert result == [("codex", "b2"), ("claude", "a"), ("cursor", "c")]


def test_route_keeps_config_order_after_selected():
    """Refs sumipan/nexus#6681 — selected engine leads, then heavy route order."""
    result = build_route(
        heavy=[("a", "ha"), ("c", "hc"), ("b", "hb")],
        light=None,
        selected_engine="a",
        selected_model="ma",
        selected_light_model=None,
        tier=None,
    )
    assert [pair[0] for pair in result] == ["a", "c", "b"]
    assert result[0] == ("a", "ma")
    assert result[1] == ("c", "hc")
    assert result[2] == ("b", "hb")


def test_light_tier_uses_light_model_or_heavy():
    """Refs sumipan/nexus#6681 — light models when allowed, else heavy per candidate."""
    allowed_models = {"cursor": {"flash"}}

    def model_allowed(engine: str, model: str) -> bool:
        return model in allowed_models.get(engine, {model})

    result = build_route(
        heavy=[("claude", "heavy-c"), ("cursor", "heavy-x")],
        light=[("claude", "light-c"), ("cursor", "light-x")],
        selected_engine="claude",
        selected_model="resolved-heavy",
        selected_light_model=None,
        tier="light",
        model_allowed=model_allowed,
    )
    assert result[0] == ("claude", "light-c")
    assert result[1] == ("cursor", "heavy-x")


def test_light_allowlist_replaces_candidate_with_heavy_pair():
    """Refs sumipan/nexus#6682 AC8 — disallowed light pair keeps position but uses heavy model."""

    def model_allowed(engine: str, model: str) -> bool:
        return not (engine == "codex" and model == "l-x")

    result = build_route(
        heavy=[("claude", "h-c"), ("codex", "h-x")],
        light=[("claude", "l-c"), ("codex", "l-x")],
        selected_engine="claude",
        selected_model="h-c",
        selected_light_model=None,
        tier="light",
        model_allowed=model_allowed,
    )
    assert result == [("claude", "l-c"), ("codex", "h-x")]


def test_paused_engines_are_dropped():
    """Refs sumipan/nexus#6681 — paused engines are omitted from the route."""
    result_with_skip = build_route(
        heavy=[("claude", "hc"), ("codex", "hx")],
        light=None,
        selected_engine="claude",
        selected_model="mc",
        selected_light_model=None,
        tier=None,
        paused=("claude",),
    )
    assert result_with_skip == [("codex", "hx")]

    assert build_route(
        heavy=[("claude", "hc"), ("codex", "hx")],
        light=None,
        selected_engine="claude",
        selected_model="mc",
        selected_light_model=None,
        tier=None,
        paused=("claude", "codex"),
    ) == []


def test_verbs_route_head_matches_resolve(monkeypatch):
    """Refs sumipan/nexus#6681 — verbs.route()[0] matches verbs.resolve()."""
    monkeypatch.setattr(
        "issuesmith.engine.load_state",
        lambda: {
            "design": {"engine": "claude", "model": "heavy-m"},
            "implementation": {"engine": "cursor", "model": "auto"},
        },
    )
    monkeypatch.setattr(
        "issuesmith.engine.HEAVY_ROUTES",
        {
            "design": (("claude", "heavy-m"), ("codex", "hx")),
            "implementation": (("claude", "hc"), ("cursor", "auto")),
        },
    )
    monkeypatch.setattr(
        "issuesmith.engine.LIGHT_ROUTES",
        {"design": (("claude", "light-m"),), "implementation": None},
    )
    monkeypatch.setattr("issuesmith.engine._light_model_allowed", lambda _e, _m: True)

    for tier in (None, "heavy", "light"):
        head = route("design", tier)[0]
        assert head == resolve("design", tier)


def test_light_allowlist_fallback_uses_selected_model_for_selected_engine():
    """Refs sumipan/nexus#6682 — selected engine falls back to the state (selected) model."""
    result = build_route(
        heavy=[("claude", "h-c"), ("codex", "h-x")],
        light=[("claude", "l-c"), ("codex", "l-x")],
        selected_engine="claude",
        selected_model="state-heavy",
        selected_light_model="bad-light",
        tier="light",
        model_allowed=lambda engine, model: model != "bad-light",
    )
    assert result == [("claude", "state-heavy"), ("codex", "l-x")]


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
