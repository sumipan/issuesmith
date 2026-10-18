"""Security validation for context_hook Issue YAML metadata (#5369 / S14)."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from issuesmith.context_hook import (
    ContextHookError,
    build_context,
    raise_metadata_violations,
    validate_context_hook_security,
    validate_issue_metadata,
)

# Leading YAML blocks from real nexus Issues (regression fixtures).
_FIXTURE_911 = textwrap.dedent(
    """\
    ```yaml
    base_branch: main
    allow_paths:
      - "src/**"
      - "tests/**"
    target_repo: sumipan/mltgnt
    ```

    Refs #905
    """
)

_FIXTURE_905 = textwrap.dedent(
    """\
    ```yaml
    base_branch: main
    allow_paths:
      - "docs/MLTGNT-ROADMAP.md"
    cp1_must_fail: true
    ```

    # tracker
    """
)

_FIXTURE_5369 = textwrap.dedent(
    """\
    ```yaml
    target_repo: sumipan/issuesmith
    base_branch: main
    allow_paths:
      - "src/issuesmith/context_hook.py"
      - "src/issuesmith/lang/en.yaml"
      - "tests/test_context_hook_validation.py"
    ```

    ## Scope
    """
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


def test_base_branch_shell_injection_rejected():
    meta = _metadata(base_branch='main"; rm -rf /')
    violations = validate_issue_metadata(meta)
    assert any(v.code == "invalid_base_branch" for v in violations)
    body = (
        "```yaml\n"
        f"base_branch: {meta['base_branch']}\n"
        "target_repo: sumipan/issuesmith\n"
        "allow_paths:\n  - src/**\n```\n"
    )
    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=body), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]
    ):
        with pytest.raises(ContextHookError):
            build_context(1)


def test_allow_paths_command_substitution_rejected():
    meta = _metadata(allow_paths=["src/$(id)/**"])
    violations = validate_issue_metadata(meta)
    assert any(
        v.code in ("invalid_allow_path", "annotation_in_path") for v in violations
    )


def test_empty_allow_paths_rejected_without_scope_gate():
    violations = validate_context_hook_security(
        {"target_repo": "sumipan/issuesmith", "base_branch": "main", "allow_paths": []}
    )
    assert any(v.code == "empty_allow_paths" for v in violations)

    body = "```yaml\nbase_branch: main\ntarget_repo: sumipan/issuesmith\n```\n"
    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=body), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]
    ):
        with pytest.raises(ContextHookError):
            build_context(99)


def test_empty_allow_paths_allowed_with_scope_gate_override():
    meta = {
        "target_repo": "sumipan/issuesmith",
        "base_branch": "main",
        "scope_gate": {"enabled": False},
    }
    assert not any(
        v.code == "empty_allow_paths" for v in validate_context_hook_security(meta)
    )
    body = textwrap.dedent(
        """\
        ```yaml
        base_branch: main
        target_repo: sumipan/issuesmith
        scope_gate:
          enabled: false
        ```

        # test
        """
    )
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(100, body=body)
    assert ctx["allow_paths"]


def test_pipeline_branch_ignored_for_untrusted_comment_author():
    comments = [
        {
            "body": "<!-- pipeline-branch: feat/issue-42-deadbeef -->",
            "author": {"login": "random-user"},
        }
    ]
    body = "# Title\n"
    with patch(
        "issuesmith.context_hook._fetch_issue_body_from_gh", return_value=body
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
    assert ctx["pipeline_id"].startswith("issue-42-")
    assert ctx["pipeline_id"] != "issue-42-deadbeef"


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


@pytest.mark.parametrize(
    "fixture_body",
    [_FIXTURE_911, _FIXTURE_905, _FIXTURE_5369],
    ids=["issue-911", "issue-905", "issue-5369"],
)
def test_legacy_nexus_issue_yaml_still_builds_context(fixture_body: str):
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(1, body=fixture_body)
    assert ctx["base_branch"] == "main"
    assert ctx["allow_paths"]


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


def test_nfd_allow_path_normalized_for_validation_only():
    nfd = "caf\u0065\u0301.md"
    meta = _metadata(allow_paths=[nfd])
    assert not validate_context_hook_security(meta)
    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        f"allow_paths:\n  - {nfd}\n```\n"
    )
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(5427, body=body)
    assert nfd in ctx["allow_paths"]


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


def test_raise_metadata_violations_attaches_all_violations():
    violations = validate_issue_metadata(
        _metadata(base_branch='main"; id', allow_paths=["src/$(id)/**"])
    )
    assert len(violations) >= 2
    with pytest.raises(ContextHookError) as exc_info:
        raise_metadata_violations(violations)
    assert exc_info.value.violations == tuple(violations)
