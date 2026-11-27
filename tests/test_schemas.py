"""Bundled JSON Schema loaders."""

from __future__ import annotations

import pytest

from issuesmith.errors import IssuesmithError
from issuesmith.schemas import SCHEMA_NAMES, load_schema


def test_load_schema_unknown_name_is_not_found() -> None:
    """Refs #8167 — unknown schema names raise NOT_FOUND with a hint naming bundled schemas."""
    with pytest.raises(IssuesmithError) as exc_info:
        load_schema("unknown")
    exc = exc_info.value
    assert exc.code == "NOT_FOUND"
    assert "issuesmith-config" in (exc.hint or "")


def test_load_schema_reads_packaged_file() -> None:
    """Refs #8167 — issuesmith-config loads from package data and includes examples."""
    schema = load_schema("issuesmith-config")
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    examples = schema.get("examples")
    assert isinstance(examples, list) and len(examples) >= 1
    assert SCHEMA_NAMES == ("issuesmith-config",)
