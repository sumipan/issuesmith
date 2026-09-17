"""tests/test_context_hook.py — unit tests for context_hook.py"""
from __future__ import annotations

import json
import logging
import textwrap
from unittest.mock import patch

import pytest

from issuesmith.context_hook import build_context, main, validate_issue_metadata


def _body(target_repo: str = "", base_branch: str = "main", allow_paths: str = "") -> str:
    lines = [f"base_branch: {base_branch}"]
    if target_repo:
        lines.append(f"target_repo: {target_repo}")
    if allow_paths:
        lines.append(f"allow_paths:\n  - {allow_paths}")
    yaml_block = "\n".join(lines)
    return f"```yaml\n{yaml_block}\n```\n\n## Purpose\ntest"


# --- AC-3: ghdag cross-repo mode ---

def test_ghdag_cross_repo_is_cross_repo():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert ctx["is_cross_repo"] == "true"


def test_ghdag_cross_repo_repo_name():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert ctx["repo_name"] == "ghdag"


def test_ghdag_cross_repo_clone_path():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert ctx["target_clone_path"] == ".claude/external/ghdag"


def test_ghdag_cross_repo_worktree_path_prefix():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert ctx["target_worktree_path"].startswith(".claude/external/ghdag/worktrees/issue-")


# --- AC-4: unsupported repo raises ValueError ---

def test_unknown_repo_raises_value_error():
    body = _body(target_repo="sumipan/unknown-repo")
    # Japanese text intentionally kept for CJK processing test
    with pytest.raises(ValueError, match="未対応"):
        build_context(999, body=body)


def test_invalid_format_raises_value_error():
    body = _body(target_repo="invalid-format")
    # Japanese text intentionally kept for CJK processing test
    with pytest.raises(ValueError, match="未対応"):
        build_context(999, body=body)


# --- regression: mltgnt still works as before ---

def test_mltgnt_still_works():
    body = _body(target_repo="sumipan/mltgnt")
    ctx = build_context(1, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "mltgnt"
    assert ctx["target_clone_path"] == ".claude/external/mltgnt"


def test_mltgnt_vscode_extension_still_works():
    body = _body(target_repo="sumipan/mltgnt-vscode-extension")
    ctx = build_context(2, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "mltgnt-vscode-extension"


def test_slack_project_supported():
    body = _body(target_repo="sumipan/slack-project")
    ctx = build_context(3, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "slack-project"
    assert ctx["target_clone_path"] == ".claude/external/slack-project"


def test_okr_core_supported():
    body = _body(target_repo="sumipan/okr-core")
    ctx = build_context(6, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "okr-core"
    assert ctx["target_clone_path"] == ".claude/external/okr-core"


def test_issuesmith_cross_repo_supported():
    """sumipan/issuesmith is in SUPPORTED_REPOS and is derived as cross-repo."""
    body = _body(target_repo="sumipan/issuesmith", allow_paths="src/**")
    violations = validate_issue_metadata({"target_repo": "sumipan/issuesmith", "allow_paths": ["src/**"]})
    assert violations == []
    ctx = build_context(7, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "issuesmith"
    assert ctx["target_clone_path"] == ".claude/external/issuesmith"
    assert ctx["target_worktree_path"].startswith(
        ".claude/external/issuesmith/worktrees/issue-"
    )


def test_diary_static_docs_supported():
    body = _body(target_repo="sumipan/diary")
    ctx = build_context(4, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "diary"
    assert ctx["target_clone_path"] == ".claude/external/diary"


def test_nexus_target_repo_is_normalized_to_native():
    """target_repo pointing at the native repo (same as issue_repo) is normalized to the native path.

    As of 250868cee44 (#2567), when target_repo == issue_repo we stop treating it as
    cross-repo. Putting it on the external clone/worktree path would make M2's
    acceptance-criteria gate inspect a feature worktree that disappears after merge,
    causing false negatives. issue_repo defaults to
    ghdag.github_client.DEFAULT_REPO = "sumipan/nexus".

    sumipan/nexus is in SUPPORTED_REPOS so the value itself is accepted
    (validate_issue_metadata does not reject it), but it is not cross-repo.
    """
    body = _body(target_repo="sumipan/nexus")
    ctx = build_context(5, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""


def test_nexus_target_repo_passes_metadata_validation():
    """Even after normalization, SUPPORTED_REPOS validation passes (target_repo required on all)."""
    from issuesmith.context_hook import parse_issue_metadata, validate_issue_metadata

    body = _body(target_repo="sumipan/nexus")
    violations = validate_issue_metadata(parse_issue_metadata(body))
    assert violations == []


# --- regression: empty/unset target_repo is in-repo (diary) mode (keep context_hook internal names) ---

def test_empty_target_repo_is_diary_mode():
    body = _body(target_repo="")
    ctx = build_context(10, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""


def test_no_target_repo_field_is_diary_mode():
    body = "```yaml\nbase_branch: main\n```\n\n## Purpose\ntest"
    ctx = build_context(11, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""


# --- Issue #990: diary_allow_paths / has_diary_changes ---

def _body_cross_repo_with_diary(diary_allow_paths=None):
    lines = [
        "base_branch: main",
        "target_repo: sumipan/ghdag",
        "allow_paths:",
        "  - src/**",
    ]
    if diary_allow_paths is not None:
        lines.append("diary_allow_paths:")
        for p in diary_allow_paths:
            lines.append(f"  - {p}")
    yaml_block = "\n".join(lines)
    return f"```yaml\n{yaml_block}\n```\n\n## Purpose\ntest"


def test_diary_allow_paths_has_diary_changes_true():
    body = _body_cross_repo_with_diary(diary_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(990, body=body)
    assert ctx["has_diary_changes"] == "true"


def test_diary_allow_paths_diary_worktree_path_format():
    import os
    body = _body_cross_repo_with_diary(diary_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(990, body=body)
    assert os.path.isabs(ctx["diary_worktree_path"])
    assert ctx["diary_worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}-diary")


def test_diary_allow_paths_single():
    body = _body_cross_repo_with_diary(diary_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(990, body=body)
    assert ctx["diary_allow_paths"] == "- workflows/issuesmith/**"


def test_diary_allow_paths_multiple():
    body = _body_cross_repo_with_diary(
        diary_allow_paths=["workflows/issuesmith/**", "tools/issuesmith/**"]
    )
    ctx = build_context(990, body=body)
    assert ctx["diary_allow_paths"] == "- workflows/issuesmith/**\n- tools/issuesmith/**"


def test_no_diary_allow_paths_has_diary_changes_false():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert ctx["has_diary_changes"] == "false"
    assert ctx["diary_worktree_path"] == ""
    assert ctx["diary_allow_paths"] == ""


def test_diary_only_mode_has_diary_changes_false():
    body = "```yaml\nbase_branch: main\n```\n\n## Purpose\ntest"
    ctx = build_context(11, body=body)
    assert ctx["has_diary_changes"] == "false"
    assert ctx["diary_worktree_path"] == ""
    assert ctx["diary_allow_paths"] == ""


def test_diary_allow_paths_without_target_repo_is_false():
    body = "```yaml\nbase_branch: main\ndiary_allow_paths:\n  - workflows/**\n```\n\n## Purpose\ntest"
    ctx = build_context(100, body=body)
    assert ctx["has_diary_changes"] == "false"


def test_lint_warning_nodo_diary_mention_no_diary_allow_paths(capsys):
    body = (
        "```yaml\n"
        "base_branch: main\n"
        "target_repo: sumipan/ghdag\n"
        "allow_paths:\n"
        "  - src/**\n"
        "```\n\n"
        # Japanese text intentionally kept for CJK processing test
        "## やらないこと\n"
        "- diary 側の変更は別途行う\n"
    )
    build_context(990, body=body)
    captured = capsys.readouterr()
    assert "diary_allow_paths" in captured.err or "やらないこと" in captured.err


# --- Issue #1719: warning log when YAML parse fails ---


def test_build_context_warns_on_missing_yaml(caplog):
    """Calling build_context() with a body that has no YAML block emits one logging.warning."""
    body = "## Purpose\ntest"
    with caplog.at_level(logging.WARNING, logger="issuesmith.context_hook"):
        build_context(42, body=body)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "42" in warnings[0].message


def test_build_context_warns_on_invalid_yaml(caplog):
    """Calling build_context() when the leading code block is json emits one logging.warning."""
    body = "```json\n{\"key\": \"value\"}\n```\n\n## Purpose\ntest"
    with caplog.at_level(logging.WARNING, logger="issuesmith.context_hook"):
        build_context(99, body=body)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "99" in warnings[0].message


# --- Issue #1757: validate_issue_metadata() ---

def test_validate_missing_target_repo():
    """No target_repo key → missing_required"""
    violations = validate_issue_metadata({"base_branch": "main", "allow_paths": ["src/**"]})
    assert len(violations) == 1
    assert violations[0].field == "target_repo"
    assert violations[0].code == "missing_required"


def test_validate_empty_target_repo():
    """Empty-string target_repo → missing_required"""
    violations = validate_issue_metadata({"target_repo": "", "allow_paths": ["src/**"]})
    assert len(violations) == 1
    assert violations[0].field == "target_repo"
    assert violations[0].code == "missing_required"


def test_validate_unsupported_target_repo():
    """Unsupported repository → unsupported_repo"""
    violations = validate_issue_metadata({"target_repo": "sumipan/unknown"})
    assert len(violations) == 1
    assert violations[0].field == "target_repo"
    assert violations[0].code == "unsupported_repo"


def test_validate_annotation_in_allow_paths():
    """Annotation mixed into allow_paths → annotation_in_path"""
    violations = validate_issue_metadata(
        # Japanese text intentionally kept for CJK processing test
        {"target_repo": "sumipan/ghdag", "allow_paths": ["(ghdag リポ) src/**"]}
    )
    assert len(violations) == 1
    assert violations[0].field == "allow_paths[0]"
    assert violations[0].code == "annotation_in_path"


def test_validate_var_tmp_in_allow_paths():
    """allow_paths contains /var/tmp/ → invalid_path_format"""
    violations = validate_issue_metadata(
        {"target_repo": "sumipan/ghdag", "allow_paths": ["/var/tmp/ghdag/"]}
    )
    assert len(violations) == 1
    assert violations[0].field == "allow_paths[0]"
    assert violations[0].code == "invalid_path_format"


def test_validate_valid_cross_repo():
    """Valid (cross-repo) → no violations"""
    violations = validate_issue_metadata(
        {"target_repo": "sumipan/ghdag", "allow_paths": ["src/**"], "base_branch": "main"}
    )
    assert violations == []


def test_validate_valid_nexus():
    """Valid (nexus) → no violations"""
    violations = validate_issue_metadata(
        {"target_repo": "sumipan/nexus", "allow_paths": ["tools/**"]}
    )
    assert violations == []


# --- Issue #2866: targets_json ---


def test_targets_json_present_and_parseable():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert "targets_json" in ctx
    targets = json.loads(ctx["targets_json"])
    assert isinstance(targets, list)
    assert len(targets) == 1
    assert targets[0]["repo"] == "sumipan/ghdag"
    assert targets[0]["primary"] is True


def test_targets_json_cross_repo_with_diary():
    body = _body_cross_repo_with_diary(diary_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(2866, body=body)
    targets = json.loads(ctx["targets_json"])
    assert len(targets) == 2
    assert targets[0]["primary"] is True
    assert targets[0]["repo"] == "sumipan/ghdag"
    assert targets[1]["primary"] is False
    assert targets[1]["repo"] == "sumipan/nexus"


def test_targets_json_backward_compat_flat_keys():
    body = _body_cross_repo_with_diary(diary_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(2866, body=body)
    assert ctx["target_repo"] == "sumipan/ghdag"
    assert ctx["is_cross_repo"] == "true"
    assert ctx["has_diary_changes"] == "true"
    assert ctx["diary_allow_paths"] == "- workflows/issuesmith/**"


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
    # Japanese text intentionally kept for CJK processing test
    assert ctx["allow_paths"] == "（制限なし）"


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


def test_diary_worktree_path_is_absolute():
    """diary_worktree_path is an absolute path (in dual mode)."""
    import os

    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        diary_allow_paths:
          - notes/**
        ```
    """)
    ctx = build_context(42, body=body)
    assert ctx["has_diary_changes"] == "true"
    assert os.path.isabs(ctx["diary_worktree_path"])
    assert ctx["diary_worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}-diary")


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
    # Japanese text intentionally kept for CJK processing test
    assert ctx["allow_paths"] == "（制限なし）"


def test_build_context_invalid_yaml():
    """When YAML cannot be parsed, fall back to defaults."""
    body = textwrap.dedent("""\
        ```yaml
        : invalid: yaml: [
        ```
    """)

    ctx = build_context(99, body=body)
    assert ctx["base_branch"] == "main"
    # Japanese text intentionally kept for CJK processing test
    assert ctx["allow_paths"] == "（制限なし）"


def test_build_context_no_yaml_block():
    """When body has no YAML block, fall back to defaults."""
    ctx = build_context(55, body="## §1 Purpose\ntest")
    assert ctx["base_branch"] == "main"
    # Japanese text intentionally kept for CJK processing test
    assert ctx["allow_paths"] == "（制限なし）"


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
    """All expected keys are present (stash_file_rel/diary_branch removed)."""
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
        "has_diary_changes",
        "diary_worktree_path",
        "diary_allow_paths",
        "targets_json",
    }
    assert set(ctx.keys()) == expected_keys


def test_stash_file_rel_not_in_output():
    """stash_file_rel key is absent from the output dict (removed)."""
    ctx = build_context(42, body="# Title")
    assert "stash_file_rel" not in ctx


def test_diary_keys_not_in_output():
    """diary_branch is removed and absent from the output dict.
    With target_repo set and diary_allow_paths unset, has_diary_changes == 'false' and diary_worktree_path is empty.
    """
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        ```
    """)
    ctx = build_context(42, body=body)
    assert "diary_branch" not in ctx
    assert ctx.get("has_diary_changes") == "false"
    assert ctx.get("diary_worktree_path") == ""


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
    # Japanese text intentionally kept for CJK processing test
    assert ctx["allow_paths"] == "（制限なし）"
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


def _comments(*bodies: str) -> list[dict]:
    return [{"body": b, "author": {"login": "bot"}, "createdAt": ""} for b in bodies]


def test_pipeline_id_restored_from_comment():
    comments = _comments("<!-- pipeline-branch: feat/issue-42-deadbeef -->")
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=comments):
        ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-deadbeef"
    assert ctx["branch"] == "feat/issue-42-deadbeef"


def test_pipeline_id_last_comment_wins():
    comments = _comments(
        "<!-- pipeline-branch: feat/issue-42-aaaaaaaa -->",
        "other",
        "<!-- pipeline-branch: feat/issue-42-bbbbbbbb -->",
    )
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=comments):
        ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"] == "issue-42-bbbbbbbb"


def test_pipeline_id_new_when_no_comments():
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(42, body="# Title")
    assert ctx["pipeline_id"].startswith("issue-42-")
    assert len(ctx["pipeline_id"]) == len("issue-42-") + 8


def test_issue_repo_defaults_to_nexus():
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(42, body="# Title")
    assert ctx["issue_repo"] == "sumipan/nexus"


def test_issue_repo_from_yaml():
    body = textwrap.dedent("""\
        ```yaml
        issue_repo: sumipan/foo
        ```
    """)
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(42, body=body)
    assert ctx["issue_repo"] == "sumipan/foo"


def test_self_target_repo_is_not_cross_repo():
    """If target_repo is the issue_repo itself, do not use the external path (#2567)"""
    body = """```yaml
target_repo: sumipan/nexus
base_branch: main
```

# Title
"""
    ctx = build_context(60, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["target_repo"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""
    assert ctx["worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}")


def test_external_target_repo_still_cross_repo():
    body = """```yaml
target_repo: sumipan/ghdag
base_branch: main
```

# Title
"""
    ctx = build_context(61, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["target_repo"] == "sumipan/ghdag"
    assert ctx["target_clone_path"] == ".claude/external/ghdag"
