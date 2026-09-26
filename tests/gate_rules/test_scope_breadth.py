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
