"""
Shared pytest fixtures for the issuesmith test suite.
"""

from unittest.mock import patch

import pytest


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
