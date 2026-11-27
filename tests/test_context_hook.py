"""tests/test_context_hook.py — unit tests for context_hook.py"""
from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from issuesmith.context_hook import (
    build_context,
    validate_issue_metadata,
)


@pytest.fixture(autouse=True)
def _no_real_branch_search(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent find_reusable_branch from reading the real repo's refs in existing tests."""
    monkeypatch.setattr(
        "issuesmith.context_hook.find_reusable_branch",
        lambda *_args, **_kwargs: None,
    )


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
    # ASCII fixture data.
    with pytest.raises(ValueError, match="target_repo"):
        build_context(999, body=body)


def test_invalid_format_raises_value_error():
    body = _body(target_repo="invalid-format")
    # ASCII fixture data.
    with pytest.raises(ValueError, match="target_repo"):
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




def test_diary_static_docs_supported():
    body = _body(target_repo="sumipan/diary")
    ctx = build_context(4, body=body)
    assert ctx["is_cross_repo"] == "true"
    assert ctx["repo_name"] == "diary"
    assert ctx["target_clone_path"] == ".claude/external/diary"






# --- regression: empty/unset target_repo is in-repo (diary) mode (keep context_hook internal names) ---



def test_no_target_repo_field_is_diary_mode():
    body = "```yaml\nbase_branch: main\n```\n\n## Purpose\ntest"
    ctx = build_context(11, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""


# --- Issue #990 / #5065: host_allow_paths / has_host_changes ---

_LEGACY_CONTEXT_KEYS = ("has_diary_changes", "diary_worktree_path", "diary_allow_paths")


def _body_cross_repo(host_allow_paths=None, legacy_allow_paths=None):
    lines = [
        "base_branch: main",
        "target_repo: sumipan/ghdag",
        "allow_paths:",
        "  - src/**",
    ]
    if host_allow_paths is not None:
        lines.append("host_allow_paths:")
        for p in host_allow_paths:
            lines.append(f"  - {p}")
    if legacy_allow_paths is not None:
        lines.append("diary_allow_paths:")
        for p in legacy_allow_paths:
            lines.append(f"  - {p}")
    yaml_block = "\n".join(lines)
    return f"```yaml\n{yaml_block}\n```\n\n## Purpose\ntest"


def test_host_allow_paths_has_host_changes_true():
    body = _body_cross_repo(host_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(990, body=body)
    assert ctx["has_host_changes"] == "true"


def test_host_allow_paths_host_worktree_path_format():
    import os
    body = _body_cross_repo(host_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(990, body=body)
    assert os.path.isabs(ctx["host_worktree_path"])
    assert ctx["host_worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}-host")


def test_host_allow_paths_single():
    body = _body_cross_repo(host_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(990, body=body)
    assert ctx["host_allow_paths"] == "- workflows/issuesmith/**"


def test_host_allow_paths_multiple():
    body = _body_cross_repo(
        host_allow_paths=["workflows/issuesmith/**", "tools/issuesmith/**"]
    )
    ctx = build_context(990, body=body)
    assert ctx["host_allow_paths"] == "- workflows/issuesmith/**\n- tools/issuesmith/**"


def test_no_host_allow_paths_has_host_changes_false():
    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(123, body=body)
    assert ctx["has_host_changes"] == "false"
    assert ctx["host_worktree_path"] == ""
    assert ctx["host_allow_paths"] == ""


def test_in_repo_mode_has_host_changes_false():
    body = "```yaml\nbase_branch: main\n```\n\n## Purpose\ntest"
    ctx = build_context(11, body=body)
    assert ctx["has_host_changes"] == "false"
    assert ctx["host_worktree_path"] == ""
    assert ctx["host_allow_paths"] == ""


def test_host_allow_paths_without_target_repo_is_false():
    body = "```yaml\nbase_branch: main\nhost_allow_paths:\n  - workflows/**\n```\n\n## Purpose\ntest"
    ctx = build_context(100, body=body)
    assert ctx["has_host_changes"] == "false"






def test_host_allow_paths_unaffected_by_legacy_key():
    body = _body_cross_repo(
        host_allow_paths=["host-only/**"], legacy_allow_paths=["legacy-only/**"]
    )
    ctx = build_context(1, body=body)
    assert ctx["host_allow_paths"] == "- host-only/**"


def test_companion_worktree_path_ignores_existing_legacy_dir(tmp_path, monkeypatch):
    import issuesmith.context_hook as ch

    wt_root = tmp_path / "wt"
    wt_root.mkdir()
    monkeypatch.setattr(ch, "_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(ch, "_WORKTREES_REL", "wt")
    pipeline_id = "issue-42-abcd1234"
    (wt_root / f"{pipeline_id}-diary").mkdir()
    assert ch._companion_worktree_path(pipeline_id) == str(wt_root / f"{pipeline_id}-host")


def test_lint_warning_nodo_host_mention_no_host_allow_paths(capsys):
    body = (
        "```yaml\n"
        "base_branch: main\n"
        "target_repo: sumipan/ghdag\n"
        "allow_paths:\n"
        "  - src/**\n"
        "```\n\n"
        "## Out of Scope\n"
        "- host-side changes are handled separately\n"
    )
    build_context(990, body=body)
    captured = capsys.readouterr()
    assert "host_allow_paths" in captured.err
    assert "'Out of Scope'" in captured.err


def test_lint_warning_ignores_legacy_side_phrase(capsys):
    body = (
        "```yaml\n"
        "base_branch: main\n"
        "target_repo: sumipan/ghdag\n"
        "allow_paths:\n"
        "  - src/**\n"
        "```\n\n"
        "## Out of Scope\n"
        "- diary-side changes are handled separately\n"
    )
    build_context(990, body=body)
    assert "host_allow_paths" not in capsys.readouterr().err




# --- Issue #1719: warning log when YAML parse fails ---






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








def test_issue_repo_defaults_to_configured_repo():
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(42, body="# Title")
    assert ctx["issue_repo"] == "sumipan/issuesmith"












# --- parse_issue_metadata_blocks: every ```yaml block, one per repo (nexus #4076) ---


_TWO_REPO_BODY = textwrap.dedent("""\
    ```yaml
    target_repo: sumipan/nexus
    base_branch: main
    allow_paths:
      - tools/foo/**
    ```

    ```yaml
    target_repo: sumipan/ghdag
    base_branch: main
    allow_paths:
      - src/ghdag/**
    ```

    ## Purpose
    two repos
""")


def test_parse_issue_metadata_blocks_returns_every_block():
    from issuesmith.context_hook import parse_issue_metadata_blocks

    blocks = parse_issue_metadata_blocks(_TWO_REPO_BODY)
    assert [b["target_repo"] for b in blocks] == ["sumipan/nexus", "sumipan/ghdag"]
    assert blocks[1]["allow_paths"] == ["src/ghdag/**"]


def test_parse_issue_metadata_blocks_skips_non_dict_and_invalid_blocks():
    from issuesmith.context_hook import parse_issue_metadata_blocks

    body = (
        "```yaml\ntarget_repo: sumipan/nexus\n```\n\n"
        "```yaml\n- a\n- b\n```\n\n"
        "```yaml\n\n```\n\n"
        "```yaml\nkey: [unclosed\n```\n\n"
        "```python\nx = {'target_repo': 'sumipan/ghdag'}\n```\n"
    )
    assert parse_issue_metadata_blocks(body) == [{"target_repo": "sumipan/nexus"}]


def test_parse_issue_metadata_blocks_empty_body():
    from issuesmith.context_hook import parse_issue_metadata_blocks

    assert parse_issue_metadata_blocks("") == []
    assert parse_issue_metadata_blocks("## Purpose\nno yaml\n") == []


def test_parse_issue_metadata_unchanged_reads_first_block_only():
    from issuesmith.context_hook import parse_issue_metadata

    assert parse_issue_metadata(_TWO_REPO_BODY)["target_repo"] == "sumipan/nexus"






def test_fetch_issue_body_from_api_uses_configured_forge_repo():
    """Refs: nexus#6608."""
    from unittest.mock import MagicMock

    from issuesmith.context_hook import _fetch_issue_body_from_api

    mock_cfg = MagicMock()
    mock_cfg.repo = "owner-a/repo-a"
    mock_forge = MagicMock()
    mock_forge.issue_get.return_value = {"body": "hello"}
    with patch("issuesmith.context_hook.get_config", return_value=mock_cfg):
        with patch("ghdag.forge.get_forge", return_value=mock_forge) as mock_get:
            assert _fetch_issue_body_from_api(1) == "hello"
    mock_get.assert_called_once_with(repo="owner-a/repo-a")


def test_raise_metadata_andon_uses_configured_forge_repo():
    """Refs: nexus#6608."""
    from unittest.mock import MagicMock

    from issuesmith.context_hook import MetadataViolation, _raise_metadata_andon

    mock_cfg = MagicMock()
    mock_cfg.repo = "owner-a/repo-a"
    violations = (
        MetadataViolation(field="allow_paths", code="empty_allow_paths", message="x"),
    )
    with patch("issuesmith.context_hook.get_config", return_value=mock_cfg):
        with patch("ghdag.forge.get_forge") as mock_get:
            with patch("issuesmith.andon.list_open", return_value=[]):
                with patch("issuesmith.andon.raise_andon"):
                    _raise_metadata_andon(1, violations)
    mock_get.assert_called_once_with(repo="owner-a/repo-a")


def test_context_hook_main_missing_arg_uses_fail_contract(capsys):
    """Refs #8129 — CLI failures use error[...] stderr lines."""
    import sys

    from issuesmith import context_hook

    with patch.object(sys, "argv", ["context_hook"]):
        with pytest.raises(SystemExit) as exc_info:
            context_hook.main()
    assert int(exc_info.value.code) == 2
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[MISSING_ARGUMENT]:")


def test_context_hook_success_stdout_unchanged(capsys):
    """Refs #8129 — successful build_context stdout stays JSON when printed."""
    import json

    body = _body(target_repo="sumipan/ghdag", allow_paths="src/**")
    ctx = build_context(50, body=body)
    print(json.dumps(ctx, ensure_ascii=False))
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["pipeline_id"].startswith("issue-50-")
