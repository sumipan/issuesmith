"""Pure route construction for (engine, model) candidate lists (#6681)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence


def build_route(
    *,
    allowed: Sequence[str],
    heavy_model: Mapping[str, str],
    light_model: Mapping[str, str],
    selected_engine: str,
    selected_model: str,
    tier: str | None,
    paused: Collection[str] = (),
    model_allowed: Callable[[str, str], bool] = lambda engine, model: True,
) -> list[tuple[str, str]]:
    """Build an ordered (engine, model) route for ghdag ``call(route=...)``."""
    paused_set = frozenset(paused)
    engines: list[str] = []
    if selected_engine not in paused_set:
        engines.append(selected_engine)
    for engine in allowed:
        if engine == selected_engine or engine in paused_set:
            continue
        engines.append(engine)

    use_light = tier == "light"
    route: list[tuple[str, str]] = []
    for engine in engines:
        if use_light:
            light = light_model.get(engine)
            if light and model_allowed(engine, light):
                route.append((engine, light))
                continue
            if engine == selected_engine:
                route.append((engine, selected_model))
            else:
                route.append((engine, heavy_model[engine]))
        elif engine == selected_engine:
            route.append((engine, selected_model))
        else:
            route.append((engine, heavy_model[engine]))
    return route
