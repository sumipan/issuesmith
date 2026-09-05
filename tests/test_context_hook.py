"""tests/test_context_hook.py — context_hook.py のユニットテスト"""
import json
import logging

import pytest

from issuesmith.context_hook import build_context, validate_issue_metadata


def _body(target_repo: str = "", base_branch: str = "main", allow_paths: str = "") -> str:
    lines = [f"base_branch: {base_branch}"]
    if target_repo:
        lines.append(f"target_repo: {target_repo}")
    if allow_paths:
        lines.append(f"allow_paths:\n  - {allow_paths}")
    yaml_block = "\n".join(lines)
    return f"```yaml\n{yaml_block}\n```\n\n## 目的\ntest"


# --- AC-3: ghdag クロスリポジトリモード ---

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


# --- AC-4: 未対応リポジトリは ValueError ---

def test_unknown_repo_raises_value_error():
    body = _body(target_repo="sumipan/unknown-repo")
    with pytest.raises(ValueError, match="未対応"):
        build_context(999, body=body)


def test_invalid_format_raises_value_error():
    body = _body(target_repo="invalid-format")
    with pytest.raises(ValueError, match="未対応"):
        build_context(999, body=body)


# --- 回帰: mltgnt は既存通り動作 ---

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
    """sumipan/issuesmith は SUPPORTED_REPOS に含まれ、cross-repo として導出される。"""
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
    """自リポ（issue_repo と同じ）を指した target_repo はネイティブ経路に正規化される。

    250868cee44（#2567）で、target_repo == issue_repo のときは cross-repo 扱いを
    やめる挙動になった。external clone/worktree 経路に乗せると、M2 の受け入れ条件
    ゲートがマージ後に消える feature worktree を契約検査して偽陰性を出すため。
    issue_repo の既定は ghdag.github_client.DEFAULT_REPO = "sumipan/nexus"。

    sumipan/nexus は SUPPORTED_REPOS に含まれるので指定自体は受理される（
    validate_issue_metadata が弾かない）が、cross-repo にはならない。
    """
    body = _body(target_repo="sumipan/nexus")
    ctx = build_context(5, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""


def test_nexus_target_repo_passes_metadata_validation():
    """正規化されても SUPPORTED_REPOS 検証は通る（target_repo は全件必須）。"""
    from issuesmith.context_hook import parse_issue_metadata, validate_issue_metadata

    body = _body(target_repo="sumipan/nexus")
    violations = validate_issue_metadata(parse_issue_metadata(body))
    assert violations == []


# --- 回帰: target_repo 空 / 未指定は diary 内モード（context_hook 内部変数名は維持） ---

def test_empty_target_repo_is_diary_mode():
    body = _body(target_repo="")
    ctx = build_context(10, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""


def test_no_target_repo_field_is_diary_mode():
    body = "```yaml\nbase_branch: main\n```\n\n## 目的\ntest"
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
    return f"```yaml\n{yaml_block}\n```\n\n## 目的\ntest"


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
    body = "```yaml\nbase_branch: main\n```\n\n## 目的\ntest"
    ctx = build_context(11, body=body)
    assert ctx["has_diary_changes"] == "false"
    assert ctx["diary_worktree_path"] == ""
    assert ctx["diary_allow_paths"] == ""


def test_diary_allow_paths_without_target_repo_is_false():
    body = "```yaml\nbase_branch: main\ndiary_allow_paths:\n  - workflows/**\n```\n\n## 目的\ntest"
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
        "## やらないこと\n"
        "- diary 側の変更は別途行う\n"
    )
    build_context(990, body=body)
    captured = capsys.readouterr()
    assert "diary_allow_paths" in captured.err or "やらないこと" in captured.err


# --- Issue #1719: YAML パース失敗時の warning ログ ---


def test_build_context_warns_on_missing_yaml(caplog):
    """YAML ブロックなし body で build_context() を呼ぶと logging.warning が 1 回出る。"""
    body = "## 目的\ntest"
    with caplog.at_level(logging.WARNING, logger="issuesmith.context_hook"):
        build_context(42, body=body)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "42" in warnings[0].message


def test_build_context_warns_on_invalid_yaml(caplog):
    """冒頭コードブロックが json の body で build_context() を呼ぶと logging.warning が 1 回出る。"""
    body = "```json\n{\"key\": \"value\"}\n```\n\n## 目的\ntest"
    with caplog.at_level(logging.WARNING, logger="issuesmith.context_hook"):
        build_context(99, body=body)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "99" in warnings[0].message


# --- Issue #1757: validate_issue_metadata() ---

def test_validate_missing_target_repo():
    """target_repo キーなし → missing_required"""
    violations = validate_issue_metadata({"base_branch": "main", "allow_paths": ["src/**"]})
    assert len(violations) == 1
    assert violations[0].field == "target_repo"
    assert violations[0].code == "missing_required"


def test_validate_empty_target_repo():
    """target_repo 空文字列 → missing_required"""
    violations = validate_issue_metadata({"target_repo": "", "allow_paths": ["src/**"]})
    assert len(violations) == 1
    assert violations[0].field == "target_repo"
    assert violations[0].code == "missing_required"


def test_validate_unsupported_target_repo():
    """未対応リポジトリ → unsupported_repo"""
    violations = validate_issue_metadata({"target_repo": "sumipan/unknown"})
    assert len(violations) == 1
    assert violations[0].field == "target_repo"
    assert violations[0].code == "unsupported_repo"


def test_validate_annotation_in_allow_paths():
    """allow_paths に注記混入 → annotation_in_path"""
    violations = validate_issue_metadata(
        {"target_repo": "sumipan/ghdag", "allow_paths": ["(ghdag リポ) src/**"]}
    )
    assert len(violations) == 1
    assert violations[0].field == "allow_paths[0]"
    assert violations[0].code == "annotation_in_path"


def test_validate_var_tmp_in_allow_paths():
    """allow_paths に /var/tmp/ → invalid_path_format"""
    violations = validate_issue_metadata(
        {"target_repo": "sumipan/ghdag", "allow_paths": ["/var/tmp/ghdag/"]}
    )
    assert len(violations) == 1
    assert violations[0].field == "allow_paths[0]"
    assert violations[0].code == "invalid_path_format"


def test_validate_valid_cross_repo():
    """正常（クロスリポ）→ violations なし"""
    violations = validate_issue_metadata(
        {"target_repo": "sumipan/ghdag", "allow_paths": ["src/**"], "base_branch": "main"}
    )
    assert violations == []


def test_validate_valid_nexus():
    """正常（nexus）→ violations なし"""
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
