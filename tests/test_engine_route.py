"""Tests for engine route construction (sumipan/nexus#6681)."""

from __future__ import annotations

from issuesmith.engine import resolve, route
from issuesmith.engine_route import build_route


def test_route_keeps_config_order_after_selected():
    """Refs sumipan/nexus#6681 — selected engine leads, then allowed order."""
    result = build_route(
        allowed=("c", "a", "b"),
        heavy_model={"a": "ha", "b": "hb", "c": "hc"},
        light_model={},
        selected_engine="a",
        selected_model="ma",
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
        allowed=("claude", "cursor"),
        heavy_model={"claude": "heavy-c", "cursor": "heavy-x"},
        light_model={"claude": "light-c", "cursor": "light-x"},
        selected_engine="claude",
        selected_model="resolved-heavy",
        tier="light",
        model_allowed=model_allowed,
    )
    assert result[0] == ("claude", "light-c")
    assert result[1] == ("cursor", "heavy-x")


def test_paused_engines_are_dropped():
    """Refs sumipan/nexus#6681 — paused engines are omitted from the route."""
    result_with_skip = build_route(
        allowed=("claude", "codex"),
        heavy_model={"claude": "hc", "codex": "hx"},
        light_model={},
        selected_engine="claude",
        selected_model="mc",
        tier=None,
        paused=("claude",),
    )
    assert result_with_skip == [("codex", "hx")]

    assert build_route(
        allowed=("claude", "codex"),
        heavy_model={"claude": "hc", "codex": "hx"},
        light_model={},
        selected_engine="claude",
        selected_model="mc",
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
        "issuesmith.engine.ROLE_ENGINES",
        {
            "design": ("claude", "codex"),
            "implementation": ("claude", "cursor"),
        },
    )
    monkeypatch.setattr(
        "issuesmith.engine.DEFAULT_MODELS",
        {
            ("design", "claude"): "heavy-m",
            ("design", "codex"): "hx",
            ("implementation", "claude"): "hc",
            ("implementation", "cursor"): "auto",
        },
    )
    monkeypatch.setattr(
        "issuesmith.engine.DEFAULT_LIGHT_MODELS",
        {("design", "claude"): "light-m"},
    )
    monkeypatch.setattr("issuesmith.engine._light_model_allowed", lambda _e, _m: True)

    for tier in (None, "heavy", "light"):
        head = route("design", tier)[0]
        assert head == resolve("design", tier)
