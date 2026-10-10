"""Unit tests for DepsFormatRules."""

from __future__ import annotations

from unittest.mock import patch

from issuesmith.gate_rules.deps_format import DepsFormatRules


def test_deps_format_reports_unparsed_prose():
    body = "## Dependencies\n\nneeds #99 first\n"
    violations = DepsFormatRules().check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "deps.unparsed_dependency_section"


def test_deps_format_passes_declared_deps():
    body = "## Dependencies\n\n| # | Issue |\n| --- | --- |\n| 1 | #99 |\n"
    assert DepsFormatRules().check(body, []) == []


def test_deps_format_does_not_call_check_dependencies():
    body = "## Dependencies\n\nneeds #99 first\n"
    with patch("issuesmith.dep_extractor.check_dependencies") as mock_check:
        DepsFormatRules().check(body, [])
    mock_check.assert_not_called()


def test_deps_format_excludes_self_issue():
    body = "## Dependencies\n\ncontext for #5061 only\n"
    assert DepsFormatRules(self_issue=5061).check(body, []) == []
