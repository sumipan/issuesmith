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
from tests.test_context_hook import _LEGACY_CONTEXT_KEYS

_no_real_branch_search = _base._no_real_branch_search


# ===========================================================================
# Moved from nexus tests/tools/issuesmith/test_issuesmith_context_hook.py
# ===========================================================================


def test_build_context_defaults():
    """When body has no YAML block, default values are returned."""
    import os

    ctx = build_context(42, body="# Title\n\nNo yaml here")

    assert ctx["pipeline_id"].startswith("issue-42-")
    assert len(ctx["pipeline_id"]) == len("issue-42-") + 8
    assert os.path.isabs(ctx["worktree_path"])
    assert ctx["worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}")
    assert ctx["branch"] == f"feat/{ctx['pipeline_id']}"
    assert ctx["base_branch"] == "main"
    # ASCII fixture data.
    assert ctx["allow_paths"]
    assert "\n" not in ctx["allow_paths"]


def test_worktree_path_is_absolute():
    """worktree_path is an absolute path (CWD-independent)."""
    import os

    ctx = build_context(42, body="# Title")
    assert os.path.isabs(ctx["worktree_path"])
    assert ctx["worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}")


def test_worktree_path_no_tools_issuesmith():
    """worktree_path does not contain tools/issuesmith."""
    ctx = build_context(42, body="# Title")
    assert "tools/issuesmith" not in ctx["worktree_path"]


def test_host_worktree_path_is_absolute():
    """host_worktree_path is an absolute path (in dual mode)."""
    import os

    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        host_allow_paths:
          - notes/**
        ```
    """)
    ctx = build_context(42, body=body)
    assert ctx["has_host_changes"] == "true"
    assert os.path.isabs(ctx["host_worktree_path"])
    assert ctx["host_worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}-host")


def test_build_context_with_metadata():
    """When body has YAML metadata, values are applied."""
    body = textwrap.dedent("""\
        ```yaml
        base_branch: develop
        allow_paths:
          - src/**
          - tests/**
        ```

        ## §1 Purpose
        Design doc for tests
    """)

    ctx = build_context(88, body=body)

    assert ctx["base_branch"] == "develop"
    assert "- src/**" in ctx["allow_paths"]
    assert "- tests/**" in ctx["allow_paths"]
    assert ctx["pipeline_id"].startswith("issue-88-")


def test_build_context_allow_paths_string():
    """allow_paths as a string is still treated as a list."""
    body = textwrap.dedent("""\
        ```yaml
        allow_paths: src/main.py
        ```
    """)

    ctx = build_context(10, body=body)
    assert ctx["allow_paths"] == "- src/main.py"


def test_build_context_empty_allow_paths():
    """Empty allow_paths list means unrestricted."""
    body = textwrap.dedent("""\
        ```yaml
        allow_paths: []
        base_branch: main
        ```
    """)

    ctx = build_context(20, body=body)
    # ASCII fixture data.
    assert ctx["allow_paths"]
    assert "\n" not in ctx["allow_paths"]


def test_build_context_invalid_yaml():
    """When YAML cannot be parsed, fall back to defaults."""
    body = textwrap.dedent("""\
        ```yaml
        : invalid: yaml: [
        ```
    """)

    ctx = build_context(99, body=body)
    assert ctx["base_branch"] == "main"
    # ASCII fixture data.
    assert ctx["allow_paths"]
    assert "\n" not in ctx["allow_paths"]


def test_build_context_no_yaml_block():
    """When body has no YAML block, fall back to defaults."""
    ctx = build_context(55, body="## §1 Purpose\ntest")
    assert ctx["base_branch"] == "main"
    # ASCII fixture data.
    assert ctx["allow_paths"]
    assert "\n" not in ctx["allow_paths"]


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


def test_build_context_all_values_are_strings():
    """All output values are strings (ghdag protocol)."""
    body = textwrap.dedent("""\
        ```yaml
        base_branch: main
        allow_paths:
          - src/**
        ```
    """)

    ctx = build_context(1, body=body)
    for key, value in ctx.items():
        assert isinstance(value, str), f"{key} is {type(value)}, expected str"


def test_build_context_output_keys():
    """All expected keys are present (stash_file_rel/diary_branch/legacy companion keys removed)."""
    ctx = build_context(42, body="# Title")
    expected_keys = {
        "pipeline_id",
        "worktree_path",
        "branch",
        "base_branch",
        "allow_paths",
        "source",
        "issue_repo",
        "target_repo",
        "repo_name",
        "target_clone_path",
        "target_worktree_path",
        "is_cross_repo",
        "has_host_changes",
        "host_worktree_path",
        "host_allow_paths",
        "targets_json",
        "previous_commits",
        "reuse_source",
    }
    assert set(ctx.keys()) == expected_keys


def test_stash_file_rel_not_in_output():
    """stash_file_rel key is absent from the output dict (removed)."""
    ctx = build_context(42, body="# Title")
    assert "stash_file_rel" not in ctx


def test_legacy_keys_not_in_output():
    """diary_branch and the legacy companion keys are absent from the output dict (#5065).
    With target_repo set and host_allow_paths unset, has_host_changes == 'false'.
    """
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        ```
    """)
    ctx = build_context(42, body=body)
    assert "diary_branch" not in ctx
    for key in _LEGACY_CONTEXT_KEYS:
        assert key not in ctx
    assert ctx["has_host_changes"] == "false"
    assert ctx["host_worktree_path"] == ""


def test_no_local_file_created(tmp_path, monkeypatch):
    """build_context() does not create jobs/issue-N-design.md."""
    monkeypatch.setattr("issuesmith.context_hook._REPO_ROOT", str(tmp_path))
    jobs_path = tmp_path / "jobs"
    jobs_path.mkdir()

    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value="# Title"):
        build_context(42)

    assert not (jobs_path / "issue-42-design.md").exists()


def test_cross_repo_defaults_when_no_target_repo():
    """When target_repo is unset: is_cross_repo=false, paths are empty strings."""
    ctx = build_context(42, body="# Title")
    assert ctx["target_repo"] == ""
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""
    assert ctx["is_cross_repo"] == "false"


def test_cross_repo_with_target_repo():
    """When target_repo is set: path variables are generated correctly."""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        base_branch: main
        ```
    """)

    ctx = build_context(42, body=body)
    assert ctx["target_repo"] == "sumipan/ghdag"
    assert ctx["repo_name"] == "ghdag"
    assert ctx["target_clone_path"] == ".claude/external/ghdag"
    assert ctx["target_worktree_path"].startswith(".claude/external/ghdag/worktrees/issue-42-")
    assert ctx["is_cross_repo"] == "true"
    assert ctx["worktree_path"] == ctx["target_worktree_path"]


def test_cross_repo_coexists_with_existing_fields():
    """target_repo + base_branch together: all existing fields are correct."""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        base_branch: develop
        allow_paths:
          - src/**
          - tests/**
        ```
    """)

    ctx = build_context(99, body=body)
    assert ctx["base_branch"] == "develop"
    assert "- src/**" in ctx["allow_paths"]
    assert "- tests/**" in ctx["allow_paths"]
    assert ctx["pipeline_id"].startswith("issue-99-")
    assert ctx["target_repo"] == "sumipan/ghdag"
    assert ctx["target_clone_path"] == ".claude/external/ghdag"
    assert ctx["is_cross_repo"] == "true"


def test_cross_repo_all_values_are_strings():
    """With target_repo set, all values are still str."""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        ```
    """)

    ctx = build_context(42, body=body)
    for key, value in ctx.items():
        assert isinstance(value, str), f"{key} is {type(value)}, expected str"


def test_cross_repo_empty_target_repo():
    """Empty-string target_repo: is_cross_repo=false."""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: ""
        ```
    """)

    ctx = build_context(42, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""


def test_cross_repo_invalid_yaml():
    """YAML parse error: is_cross_repo=false, fall back to existing defaults."""
    body = textwrap.dedent("""\
        ```yaml
        : invalid: yaml: [
        ```
    """)

    ctx = build_context(42, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["base_branch"] == "main"


def test_yaml_metadata_extraction_t1():
    """T1: correctly extract base_branch/allow_paths/target_repo."""
    body = textwrap.dedent("""\
        ```yaml
        base_branch: develop
        allow_paths:
          - src/**
        target_repo: sumipan/ghdag
        ```
        # Title
    """)
    ctx = build_context(42, body=body)
    assert ctx["base_branch"] == "develop"
    assert ctx["allow_paths"] == "- src/**"
    assert ctx["target_repo"] == "sumipan/ghdag"
    assert ctx["is_cross_repo"] == "true"


def test_yaml_none_issue_t2():
    """T2: Issue without YAML → metadata={}, base_branch=main, allow_paths=unrestricted, is_cross_repo=false."""
    body = "# Design\n\nbody only"
    ctx = build_context(42, body=body)
    assert ctx["base_branch"] == "main"
    # ASCII fixture data.
    assert ctx["allow_paths"]
    assert "\n" not in ctx["allow_paths"]
    assert ctx["is_cross_repo"] == "false"


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


def test_main_raises_andon_on_metadata_violation(capsys):
    import sys

    body = _metadata_violation_body()
    mock_client = object()
    with patch(
        "issuesmith.context_hook._fetch_issue_body_from_gh", return_value=body
    ), patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]
    ), patch("ghdag.forge.get_forge", return_value=mock_client), patch(
        "issuesmith.andon.list_open", return_value=[]
    ) as list_open, patch("issuesmith.andon.raise_andon") as raise_andon:
        original = sys.argv
        sys.argv = ["context_hook", "77"]
        try:
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        finally:
            sys.argv = original
    assert capsys.readouterr().out == ""
    list_open.assert_called_once_with(mock_client)
    raise_andon.assert_called_once()
    andon = raise_andon.call_args[0][1]
    assert isinstance(andon, Andon)
    assert andon.kind == "blocked"
    assert andon.step == "context_hook"
    assert andon.issue == 77
    assert andon.id == "context_hook:77:invalid_allow_path"


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
