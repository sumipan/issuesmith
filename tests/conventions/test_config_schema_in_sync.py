"""JSON Schema for issuesmith.yaml must match config.py key constants."""

from __future__ import annotations

from typing import Any

import yaml

import issuesmith.config as config_module
from issuesmith.schemas import load_schema
from tests.conventions._util import REPO_ROOT

_MERGE = config_module._MERGE_YAML_KEY

# (path to object schema node, expected property keys, require additionalProperties: false)
_SYNC_TABLE: list[tuple[tuple[str, ...], frozenset[str], bool]] = [
    ((), config_module._TOP_LEVEL_CONFIG_KEYS, True),
    (("properties", "engines", "additionalProperties"), config_module._ENGINE_ROLE_KEYS, True),
    (("properties", "phases", "items"), config_module._PHASE_KEYS, True),
    (("properties", "steps", "additionalProperties"), config_module._STEP_CONFIG_KEYS, True),
    (
        ("properties", "steps", "additionalProperties", "properties", "repair"),
        config_module._REPAIR_KEYS,
        True,
    ),
    (
        ("properties", "steps", "additionalProperties", "properties", "review"),
        config_module._REVIEW_KEYS,
        True,
    ),
    (
        ("properties", "steps", "additionalProperties", "properties", "tier"),
        config_module._TIER_KEYS,
        True,
    ),
    (("properties", "scope_gate"), config_module._SCOPE_GATE_KEYS, True),
    (("properties", "milestone_chain"), config_module._MILESTONE_CHAIN_KEYS, True),
    (("properties", "observe"), config_module._OBSERVE_KEYS, True),
    (("properties", "scope_coupling"), config_module._SCOPE_COUPLING_KEYS, True),
    (("properties", "scope_size"), config_module._SCOPE_SIZE_KEYS, True),
    (("properties", "andon"), config_module._ANDON_KEYS, True),
    (
        (
            "properties",
            "andon",
            "properties",
            "auto_answer",
            "items",
            "properties",
            "match",
        ),
        config_module._AUTO_ANSWER_MATCH_KEYS,
        True,
    ),
    (("properties", "api_brake"), config_module._API_BRAKE_KEYS, True),
    (("properties", "metrics"), config_module._METRICS_KEYS, True),
    (("properties", _MERGE), config_module._MERGE_KEYS, True),
    (("properties", "version"), config_module._VERSION_KEYS, True),
    (("properties", "lint"), config_module._LINT_KEYS, True),
    (("properties", "tests"), config_module._TESTS_KEYS, True),
    (("properties", "derived_allow"), config_module._DERIVED_ALLOW_KEYS, True),
    (("properties", "external_leak"), config_module._EXTERNAL_LEAK_KEYS, True),
    (("properties", "paths"), config_module._PATHS_CONFIG_KEYS, True),
    (("properties", "concurrency"), config_module._CONCURRENCY_OVERLAY_KEYS, True),
]

_REPO_SECTION_SCHEMA_PATHS: dict[str, tuple[str, ...]] = {
    "concurrency": ("properties", "concurrency"),
}


def _node_at(schema: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    node: Any = schema
    for key in path:
        node = node[key]
    assert isinstance(node, dict)
    return node


def schema_key_mismatches(
    schema: dict[str, Any],
    table: list[tuple[tuple[str, ...], frozenset[str], bool]],
) -> list[str]:
    """Return human-readable violations for each table row."""
    violations: list[str] = []
    for path, expected_keys, require_closed in table:
        label = "root" if not path else ".".join(path)
        node = schema if not path else _node_at(schema, path)
        props = node.get("properties")
        if not isinstance(props, dict):
            violations.append(f"{label}: missing properties mapping")
            continue
        schema_keys = frozenset(props.keys())
        for key in sorted(schema_keys - expected_keys):
            violations.append(f"{label}: schema-only key {key!r}")
        for key in sorted(expected_keys - schema_keys):
            violations.append(f"{label}: constant-only key {key!r}")
        if require_closed and node.get("additionalProperties") is not False:
            violations.append(f"{label}: additionalProperties must be false")
    return violations


def test_schema_keys_match_config_constants() -> None:
    """Refs #8167 — schema property keys match config constants at every mapped location."""
    schema = load_schema("issuesmith-config")
    assert schema_key_mismatches(schema, _SYNC_TABLE) == []


def test_detects_key_missing_from_schema() -> None:
    """Refs #8167 — constants-only keys are reported by schema_key_mismatches."""
    schema = load_schema("issuesmith-config")
    bogus_key = "___bogus_sync_key___"
    assert bogus_key not in config_module._TOP_LEVEL_CONFIG_KEYS
    table = [((), config_module._TOP_LEVEL_CONFIG_KEYS | {bogus_key}, True)]
    violations = schema_key_mismatches(schema, table)
    assert any(bogus_key in v and "constant-only" in v for v in violations)


def test_repo_config_keys_are_in_schema() -> None:
    """Refs #8167 — keys present in the repository issuesmith.yaml appear in the schema."""
    schema = load_schema("issuesmith-config")
    raw = yaml.safe_load((REPO_ROOT / "issuesmith.yaml").read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    top_props = set(schema["properties"].keys())
    for key in raw:
        assert key in top_props, f"top-level key {key!r} missing from schema"
    for section, schema_path in _REPO_SECTION_SCHEMA_PATHS.items():
        block = raw.get(section)
        if not isinstance(block, dict):
            continue
        props = set(_node_at(schema, schema_path)["properties"])
        for subkey in block:
            assert subkey in props, f"{section}.{subkey!r} missing from schema"
