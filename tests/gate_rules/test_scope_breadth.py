"""Unit tests for ScopeBreadthRules (CP1 allow_paths breadth gate)."""

from __future__ import annotations

import unittest.mock as mock

from issuesmith.gate_rules.scope_breadth import ScopeBreadthRules
from issuesmith.steps.scope_gate import ScopeMeasure

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


def test_within_thresholds_returns_no_violation():
    """files and lines both within threshold yields 0 violations."""
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope", return_value=_WITHIN_MEASURE
    ):
        violations = ScopeBreadthRules().check(_NEXUS_BODY, [])
    assert violations == []


def test_scope_gate_max_files_override_relaxes_threshold():
    """body YAML scope_gate: {max_files: 999} prevents Violation even when exceeded."""
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "scope_gate:\n"
        "  max_files: 999\n"
        "```\n\n"
        "## Overview\ncontent\n"
    )
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope", return_value=_EXCEEDED_MEASURE
    ):
        violations = ScopeBreadthRules().check(body, [])
    assert violations == []


def test_scope_gate_enabled_false_returns_no_violation():
    """body YAML scope_gate: {enabled: false} bypasses the check entirely."""
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "scope_gate:\n"
        "  enabled: false\n"
        "```\n\n"
        "## Overview\ncontent\n"
    )
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope", return_value=_EXCEEDED_MEASURE
    ) as mock_m:
        violations = ScopeBreadthRules().check(body, [])
    assert violations == []
    mock_m.assert_not_called()


def test_missing_measurement_root_is_a_warning():
    """cross-repo target with no clone is reported, but only as a warning (nexus #4076).

    A multi-repo milestone parent names repos that have no clone under
    .claude/external/; failing there made B1 verify unpassable.
    """
    body = (
        "```yaml\n"
        "target_repo: sumipan/nonexistent-repo\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "```\n\n"
        "## Overview\ncontent\n"
    )
    violations = ScopeBreadthRules().check(body, [])
    assert [v.rule_id for v in violations] == ["scope_breadth.root_unavailable"]
    assert violations[0].severity == "warn"


def test_root_unavailable_is_not_a_b1_verify_failed_check():
    from issuesmith.b1_verify import collect_violations, format_report

    body = (
        "```yaml\n"
        "target_repo: sumipan/nonexistent-repo\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "src/**"\n'
        "```\n\n"
        "## Overview\ncontent\n"
    )
    violations = collect_violations(body, [])
    assert "scope_breadth.root_unavailable" not in {v.rule_id for v in violations}
    assert "scope_breadth.root_unavailable" not in format_report(violations)


def test_milestone_label_skips_too_large_check():
    """scope:milestone label bypasses scope_breadth.too_large entirely (#4165)."""
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope", return_value=_EXCEEDED_MEASURE
    ) as mock_m:
        violations = ScopeBreadthRules().check(_NEXUS_BODY, ["scope:milestone"])
    assert violations == []
    mock_m.assert_not_called()


def test_milestone_label_with_other_labels_also_skips():
    """scope:milestone alongside other labels still skips too_large."""
    with mock.patch(
        "issuesmith.gate_rules.scope_breadth.measure_scope", return_value=_EXCEEDED_MEASURE
    ) as mock_m:
        violations = ScopeBreadthRules().check(_NEXUS_BODY, ["P1", "scope:milestone", "bug"])
    assert violations == []
    mock_m.assert_not_called()


def test_cross_repo_root_uses_external_dir_repo_layout(tmp_path):
    """Root is paths.external_dir/<repo> — the layout context_hook clones into.

    scope_breadth delegates root resolution to steps.scope_gate.resolve_scope_root
    (#3487 AC-1), the same function p0_worktree and gate-preflight use — see
    tests/test_scope_gate_root.py for the function's own coverage.
    """
    import unittest.mock as mock

    from issuesmith.steps.scope_gate import resolve_scope_root

    external = tmp_path / ".claude" / "external"
    (external / "issuesmith" / ".git").mkdir(parents=True)
    cfg = mock.MagicMock()
    cfg.repo = "sumipan/nexus"
    cfg.root = tmp_path
    cfg.paths.external_dir = external
    root = resolve_scope_root({"target_repo": "sumipan/issuesmith"}, cfg)
    nexus_root = resolve_scope_root({"target_repo": "sumipan/nexus"}, cfg)
    assert root == external / "issuesmith"
    assert nexus_root == tmp_path


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


def test_autofix_note_follows_custom_pack(tmp_path, monkeypatch):
    """A pack with different ASCII text yields the note in that text (same judgement)."""
    from issuesmith.language import EN

    messages = dict(EN.messages)
    messages["scope_breadth.autofix_note"] = (
        "### NARROWED [{before_files}/{before_lines}]\n"
        "was={old}\nnow={new} [{after_files}/{after_lines}]\n"
    )
    sections = dict(EN.sections)
    sections["acceptance_criteria"] = "Done When"
    _use_pack(
        tmp_path, monkeypatch, _write_pack(tmp_path, messages=messages, sections=sections)
    )

    rule = _narrow(_narrowable_body("Done When"))
    assert rule.autofix_new_allow_paths == ["src/app/one.py"]
    assert rule.autofix_note == (
        "### NARROWED [100/100]\nwas=`src/**`\nnow=`src/app/one.py` [5/500]\n"
    )


def test_autofix_note_none_word_follows_pack(tmp_path, monkeypatch):
    from issuesmith.gate_rules.scope_breadth import _format_autofix_note
    from issuesmith.language import EN

    messages = dict(EN.messages)
    messages["scope_breadth.none"] = "(nil)"
    _use_pack(tmp_path, monkeypatch, _write_pack(tmp_path, messages=messages))

    note = _format_autofix_note([], [], _EXCEEDED_MEASURE, _WITHIN_MEASURE)
    assert "- Before: (nil)" in note
    assert "- After: (nil)" in note
