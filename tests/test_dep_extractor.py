"""test_dep_extractor.py — regression tests for dependency extract/validate"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from issuesmith.dep_extractor import (
    UNPARSED_DEPENDENCY_SECTION,
    check_dependencies,
    check_issue,
    extract_dependencies,
    unparsed_dependency_refs,
)
from issuesmith.language import EN

# Inline labels come from the language pack (the tests run with the EN vocabulary).
_DEP_PREFIX = EN.sub_plan_columns[4]
_PARENT_PREFIX = EN.parent_issue_label

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


def test_section_without_table_returns_empty():
    # ASCII fixture data.
    body = "## Dependencies\n\nmentions #300\n"
    assert extract_dependencies(body) == []


def test_section_list_item_inside_code_block_ignored():
    # ASCII fixture data.
    body = "## Dependencies\n\n```\n- #400\n```\n\n- #401\n"
    assert extract_dependencies(body) == [401]


# --- unparsed_dependency_refs ---


def test_unparsed_refs_empty_without_section():
    # ASCII fixture data.
    body = "## Overview\n\nsee #300\n"
    assert unparsed_dependency_refs(body) == []


def test_unparsed_refs_reports_prose_only_mentions():
    # ASCII fixture data.
    body = "## Dependencies\n\nmentions #300 and #301\n"
    assert extract_dependencies(body) == []
    assert unparsed_dependency_refs(body) == [300, 301]


def test_unparsed_refs_reports_prose_mentions_next_to_declared():
    # ASCII fixture data.
    body = "## Dependencies\n\n- #300\n\nalso needs #302 eventually\n"
    assert unparsed_dependency_refs(body) == [302]


def test_unparsed_refs_ignores_refs_declared_by_prefix_line():
    # ASCII fixture data.
    body = f"{_DEP_PREFIX}: #300\n\n## Dependencies\n\nsee #300 above\n"
    assert unparsed_dependency_refs(body) == []


def test_unparsed_refs_ignores_mention_of_own_parent():
    # ASCII fixture data. SUB1 children say "parallel with sub 2 of the parent" in prose.
    body = f"{_PARENT_PREFIX}: #3985\n\n## Dependencies\n\nnone (parallel with sub 2 of #3985)\n"
    assert unparsed_dependency_refs(body) == []
    assert extract_dependencies(body) == []


def test_unparsed_refs_still_reports_other_prose_mentions_with_parent():
    body = f"{_PARENT_PREFIX}: #3985\n\n## Dependencies\n\nafter #4053 lands, parallel with #3985\n"
    assert unparsed_dependency_refs(body) == [4053]


def test_unparsed_refs_excludes_self_issue_when_given():
    body = "## Dependencies\n\nissue #5061 body replaced in #5061\n"
    assert unparsed_dependency_refs(body, self_issue=5061) == []
    assert unparsed_dependency_refs(body) == [5061]


def test_unparsed_refs_self_issue_does_not_hide_other_refs():
    body = "## Dependencies\n\n#5049 and #5061 in prose\n"
    assert unparsed_dependency_refs(body, self_issue=5061) == [5049]


def test_check_issue_self_reference_only_passes():
    body = "## Dependencies\n\nonly mentions #777 in prose\n"
    client = MagicMock()
    client.issue_get.return_value = {"body": body}
    result = check_issue(777, client=client)
    assert result.decision == "PASS"
    client.issue_get.assert_called_once()


def test_unparsed_refs_ignores_parent_child_lines_and_code_blocks():
    # ASCII fixture data.
    body = (
        "## Dependencies\n\n"
        f"{_PARENT_PREFIX}: #1\n"
        "```\n#2\n```\n"
    )
    assert unparsed_dependency_refs(body) == []


def test_dep_prefix_line_unaffected():
    # ASCII fixture data.
    body = f"{_DEP_PREFIX}: #500, #501\n"
    assert extract_dependencies(body) == [500, 501]


# --- check_dependencies ---


def _mock_issue_get(responses: dict[int, dict]):
    def fake_get(number, fields=None):
        return responses[number]

    return fake_get


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
    assert result.blocking_deps[0].outcome == "open"


def test_dep_closed_completed_without_labels_passes(tmp_path):
    """Refs #7965 — closed with completed satisfies deps without terminal labels."""
    from ghdag.forge import LocalForge

    client = LocalForge(tmp_path / "forge")
    dep = client.issue_create("dep", "body")
    client.issue_close(dep, reason="completed")
    result = check_dependencies([dep], client=client)
    assert result.decision == "PASS"
    assert result.dep_statuses[0].outcome == "completed"


def test_dep_closed_not_planned_blocks(tmp_path):
    """Refs #7965 — not_planned close blocks even with merge-done label."""
    from ghdag.forge import LocalForge

    client = LocalForge(tmp_path / "forge")
    dep = client.issue_create("dep", "body")
    client.issue_close(dep, reason="not_planned")
    result = check_dependencies([dep], client=client)
    assert result.decision == "BLOCK"
    assert result.blocking_deps[0].outcome == "not_planned"


def test_dep_other_namespace_done_label_passes(tmp_path):
    """Refs #7965 — foreign done label does not matter when close reason is completed."""
    from ghdag.forge import LocalForge

    client = LocalForge(tmp_path / "forge")
    dep = client.issue_create("dep", "body")
    client.issue_close(dep, reason="completed")
    result = check_dependencies([dep], client=client)
    assert result.decision == "PASS"


def test_dep_status_does_not_read_timeline_or_pr():
    """Refs #7965 — satisfaction uses issue_get fields only."""
    client = MagicMock()
    client.issue_get = _mock_issue_get(
        {
            801: {
                "state": "CLOSED",
                "state_reason": "completed",
                "title": "done",
            }
        }
    )
    check_dependencies([801], client=client)
    client.issue_timeline.assert_not_called()
    client.pr_get.assert_not_called()


def test_empty_deps_passes():
    result = check_dependencies([])
    assert result.decision == "PASS"
    assert result.deps_found == []
    assert result.blocking_deps == []


def test_unparsed_refs_block_even_with_declared_deps():
    client = MagicMock()
    result = check_dependencies([100], client=client, unparsed_refs=[300])
    assert result.decision == "BLOCK"
    assert result.deps_found == [100]
    assert result.reason == UNPARSED_DEPENDENCY_SECTION
    client.issue_get.assert_not_called()


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
                "state_reason": "completed",
                "title": "done",
            },
        ]
        import pytest

        from issuesmith import cli

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["deps", "check", "2060"])
        assert exc_info.value.code == 0

    captured = capsys.readouterr()
    import json

    data = json.loads(captured.out.strip())
    assert data["decision"] == "PASS"
    assert data["deps_found"] == [100]
    assert data["blocking_deps"] == []




