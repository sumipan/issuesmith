"""Unit tests for ScopeBreadthRules (CP1 allow_paths breadth gate)."""

from __future__ import annotations

import unittest.mock as mock

from issuesmith.gate_rules.scope_breadth import ScopeBreadthRules
from issuesmith.scope_gate import ScopeMeasure

_NEXUS_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "src/**"\n'
    "```\n\n"
    "## Overview\ncontent\n"
)

_EXCEEDED_MEASURE = ScopeMeasure(
    files=100, lines=100, by_dir={"src/": 100}, skipped_binary=0, skipped_jsonl=0
)
_WITHIN_MEASURE = ScopeMeasure(
    files=5, lines=500, by_dir={"src/": 5}, skipped_binary=0, skipped_jsonl=0
)


def test_exceeded_files_returns_violation():
    """files > max_files yields Violation with rule_id scope_breadth.too_large."""
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope", return_value=_EXCEEDED_MEASURE
    ):
        violations = ScopeBreadthRules().check(_NEXUS_BODY, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "scope_breadth.too_large"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "100" in v.message
    assert "80" in v.message
    assert "src/" in v.message










# ---------------------------------------------------------------------------
# Auto-narrowing note: Issue comment text comes from the language pack (#4476)
# ---------------------------------------------------------------------------


def _write_pack(tmp_path, **overrides):
    """Write the EN pack with ``overrides`` applied as a YAML file; return its path."""
    import dataclasses

    import yaml

    from issuesmith.language import EN, LanguagePack

    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(EN, f.name)
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, str):
            value = dict(value)
        data[f.name] = value
    data.update(overrides)
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _use_pack(tmp_path, monkeypatch, pack_path):
    from issuesmith.config import reset_config_cache

    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(f"repo: sumipan/nexus\nlanguage_pack: {pack_path}\n", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()


def _narrowable_body(ac_heading: str) -> str:
    return (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "```\n\n"
        f"## {ac_heading}\n\n"
        "```yaml\n"
        "paths_must_exist:\n"
        "  - src/app/one.py\n"
        "```\n"
    )


def _narrow(body: str) -> ScopeBreadthRules:
    rule = ScopeBreadthRules()
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope",
        side_effect=[_EXCEEDED_MEASURE, _WITHIN_MEASURE],
    ):
        assert rule.check(body, []) == []
    return rule


def test_autofix_note_uses_default_pack_text():
    from issuesmith.config import get_config

    rule = _narrow(_narrowable_body(get_config().sections["acceptance_criteria"]))
    assert rule.autofix_new_allow_paths == ["src/app/one.py"]
    assert rule.autofix_note is not None
    assert rule.autofix_note.startswith("## CP1: allow_paths narrowed automatically")
    assert "- Before: `src/**`" in rule.autofix_note
    assert "- After: `src/app/one.py` (files=5, lines=500)" in rule.autofix_note
    assert "files=100, lines=100" in rule.autofix_note



