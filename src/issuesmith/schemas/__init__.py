"""Bundled JSON Schemas for issuesmith configuration files."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from issuesmith.errors import IssuesmithError

SCHEMA_NAMES: tuple[str, ...] = ("issuesmith-config",)


def load_schema(name: str) -> dict[str, Any]:
    """Load a bundled schema by logical name (without ``.schema.json`` suffix)."""
    if name not in SCHEMA_NAMES:
        raise IssuesmithError(
            f"Unknown schema: {name!r}",
            code="NOT_FOUND",
            hint=f"schemas: {', '.join(SCHEMA_NAMES)}",
        )
    path = resources.files("issuesmith.schemas").joinpath(f"{name}.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["SCHEMA_NAMES", "load_schema"]
