"""test_dep_extractor.py — regression tests for dependency extract/validate"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from issuesmith.dep_extractor import (
    _DEP_PREFIX_RE,
    DepStatus,
    check_dependencies,
    extract_dependencies,
    get_dep_status,
    is_satisfied,
)

_DEP_PREFIX = _DEP_PREFIX_RE.pattern[1:_DEP_PREFIX_RE.pattern.index(":")]

# --- extract_dependencies ---


def test_no_dependency_section_returns_empty():
    # ASCII fixture data.
    body = "## Background\n\ndescription only\n"
    assert extract_dependencies(body) == []


def test_dependency_prefix_line_without_h2():
    # ASCII fixture data.
    body = f"{_DEP_PREFIX}: #200, #201\n"
    assert extract_dependencies(body) == [200, 201]


def test_parent_issue_line_excluded():
    # ASCII fixture data.
    body = "c89AA_c30A4_c30B7_c30E5_c30FC: #300\n"
    assert extract_dependencies(body) == []


def test_child_issue_line_excluded():
    # ASCII fixture data.
    body = "c5B50_c30A4_c30B7_c30E5_c30FC: #400\n"
    assert extract_dependencies(body) == []


def test_table_extracts_prose_excluded():
    # ASCII fixture data.
    body = (
        "## Dependencies\n\n"
        "| # | Issue |\n"
        "| --- | --- |\n"
        "| 1 | #100 |\n\n"
        "Previously failed on #200\n"
    )
    assert extract_dependencies(body) == [100]


def test_section_without_table_returns_empty():
    # ASCII fixture data.
    body = "## Dependencies\n\nmentions #300\n"
    assert extract_dependencies(body) == []


def test_section_list_format_no_longer_extracts():
    # ASCII fixture data.
    body = "## Dependencies\n\n- #400\n"
    assert extract_dependencies(body) == []


def test_dep_prefix_line_unaffected():
    # ASCII fixture data.
    body = f"{_DEP_PREFIX}: #500, #501\n"
    assert extract_dependencies(body) == [500, 501]


# --- check_dependencies ---


def _mock_issue_get(responses: dict[int, dict]):
    def fake_get(number, fields=None):
        return responses[number]

    return fake_get


def test_closed_with_merge_done_passes():
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {
            100: {
                "state": "CLOSED",
                "title": "done feature",
                "labels": [{"name": "issuesmith:merge-done"}],
            }
        }
    )
    result = check_dependencies([100], client=client)
    assert result.decision == "PASS"
    assert result.deps_found == [100]
    assert result.blocking_deps == []


def test_open_dependency_blocks():
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {500: {"state": "OPEN", "title": "open dep", "labels": []}}
    )
    result = check_dependencies([500], client=client)
    assert result.decision == "BLOCK"
    assert len(result.blocking_deps) == 1
    assert result.blocking_deps[0].issue == 500
    assert result.blocking_deps[0].state == "OPEN"
    assert result.blocking_deps[0].has_merge_done is False


def test_rescue_path_merged_pr_passes():
    # Without issuesmith management (issuesmith: label other than merge-done),
    # F2's non-issuesmith exempt absorbs the case and the rescue path cannot be tested
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {
            600: {
                "state": "CLOSED",
                "title": "legacy closed",
                "labels": [{"name": "issuesmith:develop-done"}],
            }
        }
    )
    client.issue_timeline.return_value = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {"number": 42, "pull_request": {"url": "..."}},
            },
        }
    ]
    client.pr_get.return_value = {
        "merged": True,
        "merged_at": "2026-01-01T00:00:00Z",
        "state": "CLOSED",
    }

    result = check_dependencies([600], client=client)
    assert result.decision == "PASS"
    assert result.blocking_deps == []
    assert result.dep_statuses[0].rescue_pr == 42
    client.pr_get.assert_called_once_with(42)


def test_exempt_analysis_issue_passes():
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {
            2302: {
                "state": "CLOSED",
                # ASCII fixture data.
                "title": "c3010_c969C_c5BB3_c5206_c6790_c3011issuesmith #2297 brushup stopped",
                "labels": [],
            }
        }
    )
    result = check_dependencies([2302], client=client)
    assert result.decision == "PASS"
    assert result.blocking_deps == []
    assert result.dep_statuses[0].is_exempt is True
    client.issue_timeline.assert_not_called()


def test_exempt_rejected_issue_passes():
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {
            700: {
                "state": "CLOSED",
                "title": "rejected feature",
                "labels": [{"name": "issuesmith:rejected"}],
            }
        }
    )
    result = check_dependencies([700], client=client)
    assert result.decision == "PASS"
    assert result.blocking_deps == []
    assert result.dep_statuses[0].is_exempt is True
    client.issue_timeline.assert_not_called()


def test_exempt_non_issuesmith_issue_passes():
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {
            800: {
                "state": "CLOSED",
                "title": "external tracking issue",
                "labels": [{"name": "bug"}],
            }
        }
    )
    result = check_dependencies([800], client=client)
    assert result.decision == "PASS"
    assert result.blocking_deps == []
    assert result.dep_statuses[0].is_exempt is True
    client.issue_timeline.assert_not_called()


def test_empty_deps_passes():
    result = check_dependencies([])
    assert result.decision == "PASS"
    assert result.deps_found == []
    assert result.blocking_deps == []


def test_cli_check_outputs_json(capsys):
    # ASCII fixture data.
    body = (
        "## Dependencies\n\n"
        "| # | Issue |\n"
        "| --- | --- |\n"
        "| 1 | #100 |\n"
    )
    with patch("issuesmith.dep_extractor.get_forge") as mock_forge:
        client = mock_forge.return_value
        client.issue_get.side_effect = [
            {"body": body},
            {
                "state": "CLOSED",
                "title": "done",
                "labels": [{"name": "issuesmith:merge-done"}],
            },
        ]
        from issuesmith.dep_extractor import main

        with patch("sys.argv", ["dep_extractor", "check", "2060"]):
            main()

    captured = capsys.readouterr()
    import json

    data = json.loads(captured.out.strip())
    assert data["decision"] == "PASS"
    assert data["deps_found"] == [100]
    assert data["blocking_deps"] == []


def test_is_exempt_closed_milestone_with_sub_done():
    """A split-complete milestone (scope:milestone + sub-done) is treated as resolved when CLOSED (2026-09-05, #2821 blocked on #2820)."""
    from issuesmith.dep_extractor import _is_exempt
    labels = [{"name": "scope:milestone"}, {"name": "issuesmith:sub-done"}, {"name": "issuesmith:draft-done"}]
    assert _is_exempt("issuesmith extract phase 1", labels) is True


def test_is_exempt_milestone_without_sub_done_is_not_exempt():
    """Incomplete milestone split is not exempt (sub-done required)."""
    from issuesmith.dep_extractor import _is_exempt
    labels = [{"name": "scope:milestone"}, {"name": "issuesmith:draft-done"}]
    assert _is_exempt("some milestone", labels) is False


def test_get_dep_status_and_is_satisfied_public_api():
    client = MagicMock()
    client.issue_get.return_value = {
        "state": "CLOSED",
        "title": "done",
        "labels": [{"name": "issuesmith:merge-done"}],
    }
    status = get_dep_status(client, 100)
    assert isinstance(status, DepStatus)
    assert is_satisfied(status) is True
