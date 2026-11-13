"""tests/test_context_hook.py — unit tests for context_hook.py"""
from __future__ import annotations

import json
import logging
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


def test_issuesmith_cross_repo_supported():
    """sumipan/issuesmith is in SUPPORTED_REPOS and is derived as cross-repo."""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/issuesmith
        issue_repo: sumipan/nexus
        allow_paths:
          - src/**
        ```
    """)
    violations = validate_issue_metadata(
        {
            "target_repo": "sumipan/issuesmith",
            "issue_repo": "sumipan/nexus",
            "allow_paths": ["src/**"],
        }
    )
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
    causing false negatives. issue_repo defaults to issuesmith.yaml ``repo``.

    sumipan/issuesmith is in SUPPORTED_REPOS so the value itself is accepted
    (validate_issue_metadata does not reject it), but it is not cross-repo.
    """
    body = _body(target_repo="sumipan/issuesmith")
    ctx = build_context(5, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""


def test_nexus_target_repo_passes_metadata_validation():
    """Even after normalization, SUPPORTED_REPOS validation passes (target_repo required on all)."""
    from issuesmith.context_hook import parse_issue_metadata, validate_issue_metadata

    body = _body(target_repo="sumipan/issuesmith")
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


def test_legacy_allow_paths_key_is_ignored():
    """#5065: the removed legacy YAML key is unknown; the issue runs in single-repo mode."""
    body = _body_cross_repo(legacy_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(5065, body=body)
    assert ctx["has_host_changes"] == "false"
    assert ctx["host_worktree_path"] == ""
    assert ctx["host_allow_paths"] == ""
    targets = json.loads(ctx["targets_json"])
    assert len(targets) == 1
    assert targets[0]["repo"] == "sumipan/ghdag"
    assert targets[0]["primary"] is True
    for key in _LEGACY_CONTEXT_KEYS:
        assert key not in ctx


def test_host_allow_paths_emits_no_legacy_context_keys():
    body = _body_cross_repo(host_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(5051, body=body)
    assert ctx["has_host_changes"] == "true"
    assert ctx["host_allow_paths"] == "- workflows/issuesmith/**"
    assert ctx["host_worktree_path"].endswith(f"/{ctx['pipeline_id']}-host")
    for key in _LEGACY_CONTEXT_KEYS:
        assert key not in ctx


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


def test_lint_warning_reads_out_of_scope_heading_from_language_pack(
    capsys, tmp_path, monkeypatch
):
    """A pack with a different ASCII out-of-scope heading drives the host lint warning."""
    import dataclasses

    from issuesmith import config as config_module
    from issuesmith.config import reset_config_cache
    from issuesmith.language import EN

    custom = dataclasses.replace(EN, out_of_scope_heading="Not Doing")
    monkeypatch.setattr(config_module, "EN", custom)
    reset_config_cache()
    try:
        body = (
            "```yaml\n"
            "base_branch: main\n"
            "target_repo: sumipan/ghdag\n"
            "allow_paths:\n"
            "  - src/**\n"
            "```\n\n"
            "## Not Doing\n"
            "- update host docs\n"
        )
        build_context(990, body=body)
        assert "'Not Doing'" in capsys.readouterr().err

        body_default = body.replace("## Not Doing", "## Out of Scope")
        build_context(990, body=body_default)
        assert "host_allow_paths" not in capsys.readouterr().err
    finally:
        reset_config_cache()


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
        # ASCII fixture data.
        {"target_repo": "sumipan/ghdag", "allow_paths": ["(ghdag c30EA_c30DD) src/**"]}
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


def test_targets_json_cross_repo_with_host():
    body = _body_cross_repo(host_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(2866, body=body)
    targets = json.loads(ctx["targets_json"])
    assert len(targets) == 2
    assert targets[0]["primary"] is True
    assert targets[0]["repo"] == "sumipan/ghdag"
    assert targets[1]["primary"] is False
    assert targets[1]["repo"] == "sumipan/issuesmith"


def test_targets_json_backward_compat_flat_keys():
    body = _body_cross_repo(host_allow_paths=["workflows/issuesmith/**"])
    ctx = build_context(2866, body=body)
    assert ctx["target_repo"] == "sumipan/ghdag"
    assert ctx["is_cross_repo"] == "true"
    assert ctx["has_host_changes"] == "true"
    assert ctx["host_allow_paths"] == "- workflows/issuesmith/**"


def test_issue_repo_defaults_to_configured_repo():
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx = build_context(42, body="# Title")
    assert ctx["issue_repo"] == "sumipan/issuesmith"


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
target_repo: sumipan/issuesmith
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


def test_unrestricted_allow_paths_round_trips_through_publish_and_scope_gate():
    """No allow_paths -> the sentinel ops.publish and scope_gate read as 'no filter' (#4471)."""
    from issuesmith.ops.publish import _parse_allow_paths
    from issuesmith.scope_gate import parse_allow_paths_from_ctx

    body = "```yaml\nbase_branch: main\ntarget_repo: sumipan/ghdag\n```\n\n## Purpose\ntest"
    ctx = build_context(4471, body=body)
    assert ctx["allow_paths"]
    assert _parse_allow_paths(ctx["allow_paths"]) is None
    assert parse_allow_paths_from_ctx(ctx["allow_paths"]) == []


def test_issue_repo_explicit_overrides_configured_default():
    """Refs: nexus#6608."""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        issue_repo: owner-b/repo-b
        allow_paths:
          - src/**
        ```
    """)
    with patch(
        "issuesmith.context_hook._fetch_issue_comments_from_api",
        return_value=[],
    ):
        ctx = build_context(6608, body=body)
    assert ctx["issue_repo"] == "owner-b/repo-b"


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
