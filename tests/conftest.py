"""
Shared pytest fixtures for the issuesmith test suite.
"""

from unittest.mock import patch

import pytest

import issuesmith.config as config_module

_ENGLISH_SECTIONS = {
    "acceptance_criteria": "Acceptance Criteria",
    "migration": "Migration Steps",
    "migration_state_survey": "Runtime State Survey",
    "sub_plan": "Sub-issue Plan",
    "design": "Design",
    "background": "Background",
    "dependencies": "Dependencies",
    "impact_survey": "Impact Survey",
    "milestone": "Milestone",
    "changed_files": "Changed Files",
}
_ENGLISH_SUBSECTIONS = ("Scope", "Design Policy", "Changed Files", "Acceptance Criteria")


@pytest.fixture(autouse=True)
def english_section_defaults(monkeypatch):
    """Exercise section parsing with ASCII-only configured names."""
    monkeypatch.setattr(config_module, "_DEFAULT_SECTIONS", _ENGLISH_SECTIONS)
    monkeypatch.setattr(
        config_module,
        "_DEFAULT_SUB_DESIGN_SUBSECTIONS",
        _ENGLISH_SUBSECTIONS,
    )
    config_module.reset_config_cache()
    yield
    config_module.reset_config_cache()


@pytest.fixture(autouse=True)
def no_gh_fetch(request):
    """By default, mock _fetch_issue_body_from_gh to return None.

    Prevents tests that use a local design.md from accidentally fetching
    a real GitHub Issue (no gh CLI dependency). Override with patch when
    explicitly testing gh fetch behavior.
    """
    if "gh_fetch" in request.keywords:
        yield
        return
    with (
        patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=None),
        patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]),
    ):
        yield
