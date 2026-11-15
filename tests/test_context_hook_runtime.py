"""context_hook: context output, CLI entry point and andon on metadata errors."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from issuesmith.andon import Andon
from issuesmith.context_hook import (
    ContextHookError,
    _metadata_andon_id,
    build_context,
    main,
    validate_issue_metadata,
)
from tests import test_context_hook as _base

_no_real_branch_search = _base._no_real_branch_search


# ===========================================================================
# Moved from nexus tests/tools/issuesmith/test_issuesmith_context_hook.py
# ===========================================================================






def test_worktree_path_no_tools_issuesmith():
    """worktree_path does not contain tools/issuesmith."""
    ctx = build_context(42, body="# Title")
    assert "tools/issuesmith" not in ctx["worktree_path"]






def test_build_context_allow_paths_string():
    """allow_paths as a string is still treated as a list."""
    body = textwrap.dedent("""\
        ```yaml
        allow_paths: src/main.py
        ```
    """)

    ctx = build_context(10, body=body)
    assert ctx["allow_paths"] == "- src/main.py"








def test_build_context_unique_pipeline_ids():
    """Without pipeline-branch in comments, a different pipeline_id is generated each time for the same issue_number."""
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx1 = build_context(42, body="# Title")
        ctx2 = build_context(42, body="# Title")
    assert ctx1["pipeline_id"] != ctx2["pipeline_id"]


def test_main_no_args(capsys):
    """With no args, print usage to stderr and exit 1."""
    import sys

    with pytest.raises(SystemExit, match="1"):
        original = sys.argv
        sys.argv = ["context_hook"]
        try:
            main()
        finally:
            sys.argv = original


def test_main_invalid_arg(capsys):
    """Non-integer args print an error to stderr and exit 1."""
    import sys

    with pytest.raises(SystemExit, match="1"):
        original = sys.argv
        sys.argv = ["context_hook", "not-a-number"]
        try:
            main()
        finally:
            sys.argv = original






def test_stash_file_rel_not_in_output():
    """stash_file_rel key is absent from the output dict (removed)."""
    ctx = build_context(42, body="# Title")
    assert "stash_file_rel" not in ctx




def test_no_local_file_created(tmp_path, monkeypatch):
    """build_context() does not create jobs/issue-N-design.md."""
    monkeypatch.setattr("issuesmith.context_hook._REPO_ROOT", str(tmp_path))
    jobs_path = tmp_path / "jobs"
    jobs_path.mkdir()

    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value="# Title"):
        build_context(42)

    assert not (jobs_path / "issue-42-design.md").exists()


















def test_gh_fetch_failure_exits_immediately():
    """T6: _fetch_issue_body_from_gh() returns None → SystemExit non-zero; no local-file fallback."""
    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=None):
        with pytest.raises(SystemExit) as exc_info:
            build_context(42)
        assert exc_info.value.code != 0


def test_main_exits_on_api_failure():
    """When the API fails in main(), exit non-zero."""
    import sys

    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=None):
        original = sys.argv
        sys.argv = ["context_hook", "42"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code != 0
        finally:
            sys.argv = original


def _metadata_violation_body() -> str:
    return textwrap.dedent(
        """\
        ```yaml
        target_repo: sumipan/issuesmith
        base_branch: main
        allow_paths:
          - src/;rm/**
        ```

        ## Purpose
        test
        """
    )


def test_metadata_andon_id_dedupes_and_sorts_codes():
    violations = validate_issue_metadata(
        {
            "target_repo": "sumipan/issuesmith",
            "base_branch": 'main"; id',
            "allow_paths": ["src/;x/**", "src/$y/**"],
        }
    )
    assert _metadata_andon_id(99, tuple(violations)) == (
        "context_hook:99:invalid_allow_path+invalid_base_branch"
    )




def test_main_skips_andon_when_open_duplicate_exists(capsys):
    import sys

    body = _metadata_violation_body()
    existing = Andon(
        id="context_hook:77:invalid_allow_path",
        kind="blocked",
        issue=77,
        step="context_hook",
        summary="existing",
    )
    with patch(
        "issuesmith.context_hook._fetch_issue_body_from_gh", return_value=body
    ), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]
    ), patch("ghdag.forge.get_forge", return_value=object()), patch(
        "issuesmith.andon.list_open", return_value=[existing]
    ), patch("issuesmith.andon.raise_andon") as raise_andon:
        original = sys.argv
        sys.argv = ["context_hook", "77"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        finally:
            sys.argv = original
    raise_andon.assert_not_called()
    assert capsys.readouterr().out == ""


def test_main_andon_failure_adds_warning(capsys):
    import sys

    body = _metadata_violation_body()
    with patch(
        "issuesmith.context_hook._fetch_issue_body_from_gh", return_value=body
    ), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]
    ), patch("ghdag.forge.get_forge", side_effect=RuntimeError("no forge")):
        original = sys.argv
        sys.argv = ["context_hook", "77"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        finally:
            sys.argv = original
    err = capsys.readouterr().err
    assert "Warning: failed to raise context_hook andon for #77:" in err
    assert "Error:" in err


def test_main_no_andon_for_string_only_context_hook_error():
    import sys

    with patch("issuesmith.context_hook._raise_metadata_andon") as mock_andon, patch(
        "issuesmith.context_hook.build_context",
        side_effect=ContextHookError("language pack missing"),
    ):
        original = sys.argv
        sys.argv = ["context_hook", "1"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        finally:
            sys.argv = original
    mock_andon.assert_not_called()
