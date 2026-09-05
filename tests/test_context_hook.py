"""tests/test_context_hook.py — context_hook.py のユニットテスト"""
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


# ===========================================================================
# nexus tests/tools/issuesmith/test_issuesmith_context_hook.py から移設
# ===========================================================================


def test_build_context_defaults():
    """YAML ブロックがない body の場合、デフォルト値が返る。"""
    import os

    ctx = build_context(42, body="# Title\n\nNo yaml here")

    assert ctx["pipeline_id"].startswith("issue-42-")
    assert len(ctx["pipeline_id"]) == len("issue-42-") + 8
    assert os.path.isabs(ctx["worktree_path"])
    assert ctx["worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}")
    assert ctx["branch"] == f"feat/{ctx['pipeline_id']}"
    assert ctx["base_branch"] == "main"
    assert ctx["allow_paths"] == "（制限なし）"


def test_worktree_path_is_absolute():
    """worktree_path が絶対パスであること（CWD 非依存）。"""
    import os

    ctx = build_context(42, body="# Title")
    assert os.path.isabs(ctx["worktree_path"])
    assert ctx["worktree_path"].endswith(f"/.claude/worktrees/{ctx['pipeline_id']}")


def test_worktree_path_no_tools_issuesmith():
    """worktree_path に tools/issuesmith が含まれないこと。"""
    ctx = build_context(42, body="# Title")
    assert "tools/issuesmith" not in ctx["worktree_path"]


def test_diary_worktree_path_is_absolute():
    """diary_worktree_path が絶対パスであること（dual mode 時）。"""
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
    """body に YAML メタデータがある場合、値が反映される。"""
    body = textwrap.dedent("""\
        ```yaml
        base_branch: develop
        allow_paths:
          - src/**
          - tests/**
        ```

        ## §1 目的
        テスト用設計書
    """)

    ctx = build_context(88, body=body)

    assert ctx["base_branch"] == "develop"
    assert "- src/**" in ctx["allow_paths"]
    assert "- tests/**" in ctx["allow_paths"]
    assert ctx["pipeline_id"].startswith("issue-88-")


def test_build_context_allow_paths_string():
    """allow_paths が文字列の場合でもリストとして扱われる。"""
    body = textwrap.dedent("""\
        ```yaml
        allow_paths: src/main.py
        ```
    """)

    ctx = build_context(10, body=body)
    assert ctx["allow_paths"] == "- src/main.py"


def test_build_context_empty_allow_paths():
    """allow_paths が空リストの場合、制限なしになる。"""
    body = textwrap.dedent("""\
        ```yaml
        allow_paths: []
        base_branch: main
        ```
    """)

    ctx = build_context(20, body=body)
    assert ctx["allow_paths"] == "（制限なし）"


def test_build_context_invalid_yaml():
    """YAML がパースできない場合、デフォルト値にフォールバックする。"""
    body = textwrap.dedent("""\
        ```yaml
        : invalid: yaml: [
        ```
    """)

    ctx = build_context(99, body=body)
    assert ctx["base_branch"] == "main"
    assert ctx["allow_paths"] == "（制限なし）"


def test_build_context_no_yaml_block():
    """YAML ブロックがない body の場合、デフォルト値にフォールバックする。"""
    ctx = build_context(55, body="## §1 目的\nテスト")
    assert ctx["base_branch"] == "main"
    assert ctx["allow_paths"] == "（制限なし）"


def test_build_context_unique_pipeline_ids():
    """コメントに pipeline-branch が無いとき、同じ issue_number でも毎回異なる pipeline_id が生成される。"""
    with patch("issuesmith.context_hook._fetch_issue_comments_from_api", return_value=[]):
        ctx1 = build_context(42, body="# Title")
        ctx2 = build_context(42, body="# Title")
    assert ctx1["pipeline_id"] != ctx2["pipeline_id"]


def test_main_no_args(capsys):
    """引数なしの場合、usage を stderr に出力して exit 1。"""
    import sys

    with pytest.raises(SystemExit, match="1"):
        original = sys.argv
        sys.argv = ["context_hook"]
        try:
            main()
        finally:
            sys.argv = original


def test_main_invalid_arg(capsys):
    """整数でない引数の場合、エラーメッセージを stderr に出力して exit 1。"""
    import sys

    with pytest.raises(SystemExit, match="1"):
        original = sys.argv
        sys.argv = ["context_hook", "not-a-number"]
        try:
            main()
        finally:
            sys.argv = original


def test_build_context_all_values_are_strings():
    """全ての出力値が文字列であること（ghdag プロトコル準拠）。"""
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
    """期待するキーがすべて含まれていること（stash_file_rel/diary_branch は廃止）。"""
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
    """stash_file_rel キーが出力辞書に存在しない（廃止済み）。"""
    ctx = build_context(42, body="# Title")
    assert "stash_file_rel" not in ctx


def test_diary_keys_not_in_output():
    """diary_branch は廃止済みで出力辞書に存在しない。
    target_repo あり + diary_allow_paths 未設定時は has_diary_changes == 'false' かつ diary_worktree_path は空文字。
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
    """build_context() は jobs/issue-N-design.md を作成しない。"""
    monkeypatch.setattr("issuesmith.context_hook._REPO_ROOT", str(tmp_path))
    jobs_path = tmp_path / "jobs"
    jobs_path.mkdir()

    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value="# Title"):
        build_context(42)

    assert not (jobs_path / "issue-42-design.md").exists()


def test_cross_repo_defaults_when_no_target_repo():
    """target_repo 未指定時: is_cross_repo=false, パスは空文字列。"""
    ctx = build_context(42, body="# Title")
    assert ctx["target_repo"] == ""
    assert ctx["repo_name"] == ""
    assert ctx["target_clone_path"] == ""
    assert ctx["target_worktree_path"] == ""
    assert ctx["is_cross_repo"] == "false"


def test_cross_repo_with_target_repo():
    """target_repo 指定時: パス変数が正しく生成される。"""
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
    """target_repo + base_branch 同時指定: 既存フィールドがすべて正しい。"""
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
    """target_repo 指定時も全 value が str 型。"""
    body = textwrap.dedent("""\
        ```yaml
        target_repo: sumipan/ghdag
        ```
    """)

    ctx = build_context(42, body=body)
    for key, value in ctx.items():
        assert isinstance(value, str), f"{key} is {type(value)}, expected str"


def test_cross_repo_empty_target_repo():
    """target_repo が空文字列: is_cross_repo=false。"""
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
    """YAML パースエラー: is_cross_repo=false、既存デフォルト値にフォールバック。"""
    body = textwrap.dedent("""\
        ```yaml
        : invalid: yaml: [
        ```
    """)

    ctx = build_context(42, body=body)
    assert ctx["is_cross_repo"] == "false"
    assert ctx["base_branch"] == "main"


def test_yaml_metadata_extraction_t1():
    """T1: base_branch/allow_paths/target_repo を正しく抽出する。"""
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
    """T2: YAML なし Issue では metadata={}, base_branch=main, allow_paths=制限なし, is_cross_repo=false。"""
    body = "# Design\n\n本文のみ"
    ctx = build_context(42, body=body)
    assert ctx["base_branch"] == "main"
    assert ctx["allow_paths"] == "（制限なし）"
    assert ctx["is_cross_repo"] == "false"


def test_gh_fetch_failure_exits_immediately():
    """T6: _fetch_issue_body_from_gh() が None を返す → SystemExit で非ゼロ終了。ローカルファイルフォールバックなし。"""
    with patch("issuesmith.context_hook._fetch_issue_body_from_gh", return_value=None):
        with pytest.raises(SystemExit) as exc_info:
            build_context(42)
        assert exc_info.value.code != 0


def test_main_exits_on_api_failure():
    """main() で API が失敗した場合、非ゼロで終了する。"""
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
    """target_repo が issue_repo 自身なら external 経路に乗せない（#2567）"""
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
