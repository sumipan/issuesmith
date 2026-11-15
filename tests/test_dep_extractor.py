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
    assert result.blocking_deps[0].has_terminal_label is False


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
                "title": "done",
                "labels": [{"name": "issuesmith:merge-done"}],
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




# --- language pack vocabulary ---


def _use_custom_pack(tmp_path, monkeypatch, **overrides):
    """Write an ASCII pack that differs from EN to tmp YAML and make it the default."""
    import dataclasses

    import yaml

    import issuesmith.config as config_module
    from issuesmith.config import reset_config_cache
    from issuesmith.language import LanguagePack, load_language_pack

    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(EN, f.name)
        data[f.name] = list(value) if isinstance(value, tuple) else (
            value if isinstance(value, str) else dict(value)
        )
    data.update(overrides)
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(config_module, "EN", load_language_pack(path))
    reset_config_cache()


