"""Pure route construction for (engine, model) candidate lists (#6681, #6682)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence


def build_route(
    *,
    heavy: Sequence[tuple[str, str]],
    light: Sequence[tuple[str, str]] | None,
    selected_engine: str,
    selected_model: str,
    selected_light_model: str | None,
    tier: str | None,
    paused: Collection[str] = (),
    model_allowed: Callable[[str, str], bool] = lambda engine, model: True,
) -> list[tuple[str, str]]:
    """Build an ordered (engine, model) route for ghdag ``call(route=...)``."""
    paused_set = frozenset(paused)
    use_light = tier == "light"
    heavy_by_engine = {engine: model for engine, model in heavy}

    if use_light and light is not None:
        source = list(light)
    else:
        source = list(heavy)

    light_by_engine = {engine: model for engine, model in (light or ())}
    selected_in_source = any(engine == selected_engine for engine, _ in source)
    reorder_selected = selected_in_source or not (use_light and light is not None)

    engines_ordered: list[str] = []
    if reorder_selected and selected_engine not in paused_set:
        engines_ordered.append(selected_engine)
    for engine, _ in source:
        if engine == selected_engine or engine in paused_set:
            continue
        engines_ordered.append(engine)
    if not reorder_selected:
        engines_ordered = [engine for engine, _ in source if engine not in paused_set]

    route: list[tuple[str, str]] = []
    for engine in engines_ordered:
        if use_light and light is not None:
            model = light_by_engine.get(engine, heavy_by_engine[engine])
            if engine == selected_engine and selected_light_model:
                model = selected_light_model
            if not model_allowed(engine, model):
                model = selected_model if engine == selected_engine else heavy_by_engine[engine]
            route.append((engine, model))
        elif use_light and light is None:
            if engine == selected_engine:
                if selected_light_model and model_allowed(engine, selected_light_model):
                    model = selected_light_model
                else:
                    model = selected_model
            else:
                model = heavy_by_engine[engine]
            route.append((engine, model))
        elif engine == selected_engine:
            route.append((engine, selected_model))
        else:
            route.append((engine, heavy_by_engine[engine]))
    return route
