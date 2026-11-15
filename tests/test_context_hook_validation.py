"""Security validation for context_hook Issue YAML metadata (#5369 / S14)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from issuesmith.context_hook import (
    build_context,
    validate_context_hook_security,
    validate_issue_metadata,
)


@pytest.fixture(autouse=True)
def _no_branch_search(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "issuesmith.context_hook.find_reusable_branch",
        lambda *_a, **_k: None,
    )


def _metadata(**kwargs: object) -> dict:
    base = {
        "target_repo": "sumipan/issuesmith",
        "base_branch": "main",
        "allow_paths": ["src/**"],
    }
    base.update(kwargs)
    return base


def test_allow_paths_command_substitution_rejected():
    meta = _metadata(allow_paths=["src/$(id)/**"])
    violations = validate_issue_metadata(meta)
    assert any(
        v.code in ("invalid_allow_path", "annotation_in_path") for v in violations
    )


def test_pipeline_branch_accepted_for_issue_author():
    comments = [
        {
            "body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->",
            "author": {"login": "issue-owner"},
        }
    ]
    with patch(
        "issuesmith.context_hook._fetch_issue_body_from_gh", return_value="# Title\n"
    ), patch(
        "issuesmith.context_hook._fetch_issue_author_login",
        return_value="issue-owner",
    ), patch(
        "issuesmith.context_hook._github_token_login",
        return_value="token-bot",
    ), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api",
        return_value=comments,
    ):
        ctx = build_context(42)
    assert ctx["pipeline_id"] == "issue-42-deadbeef"


def test_pipeline_branch_author_as_login_string():
    """get_issue_comments() returns author as a plain login string."""
    trusted = [
        {"body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->", "author": "token-bot"}
    ]
    untrusted = [
        {"body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->", "author": "random-user"}
    ]
    with patch(
        "issuesmith.context_hook._fetch_issue_body_from_gh", return_value="# Title\n"
    ), patch(
        "issuesmith.context_hook._fetch_issue_author_login",
        return_value="issue-owner",
    ), patch(
        "issuesmith.context_hook._github_token_login",
        return_value="token-bot",
    ), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api",
        side_effect=[trusted, untrusted],
    ):
        assert build_context(42)["pipeline_id"] == "issue-42-deadbeef"
        assert build_context(42)["pipeline_id"] != "issue-42-deadbeef"


_UNICODE_ALLOW_PATHS = [
    "docs/caf\u00e9.md",
    "agents/\u03a9mega.md",
    "notes/\u00f1and\u00fa/**",
]


@pytest.mark.parametrize("path", _UNICODE_ALLOW_PATHS)
def test_unicode_allow_paths_accepted(path: str):
    meta = _metadata(allow_paths=[path])
    sec = validate_context_hook_security(meta)
    assert not any(v.code == "invalid_allow_path" for v in sec)
    full = validate_issue_metadata(meta)
    assert not any(v.code == "invalid_allow_path" for v in full)


@pytest.mark.parametrize(
    "path,expected_code",
    [
        ("src/a b/**", "invalid_allow_path"),
        ("src/a\u00a0b/**", "invalid_allow_path"),
        ("src/$id/**", "invalid_allow_path"),
        ("src/;rm/**", "invalid_allow_path"),
        ("src/|wc/**", "invalid_allow_path"),
        ("src/&bg/**", "invalid_allow_path"),
        ("src/`id`/**", "invalid_allow_path"),
        ('src/"x"/**', "invalid_allow_path"),
        ("src/<x>/**", "invalid_allow_path"),
        ("src/\x07/**", "invalid_allow_path"),
        ("\u0301", "invalid_allow_path"),
    ],
)
def test_allow_path_rejections(path: str, expected_code: str):
    violations = validate_context_hook_security(_metadata(allow_paths=[path]))
    assert any(v.code == expected_code for v in violations)


