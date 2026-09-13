"""Fixture tests for issuesmith.steps.sub1_create (#3166).

Issue create fixtures were captured 2026-09-13 from live GitHub REST via
``GitHubClient`` (CLAUDE.md §10):

  issue_get(3000) after equivalent POST /repos/.../issues success shape
  → number/title/state/html_url/id

  issue_create(title="", body=...)
  → GitHubApiError: GitHub API POST .../issues failed (422): Validation Failed
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.config import reset_config_cache
from issuesmith.engine import _extract_status_values
from issuesmith.milestone import (
    check_v1_target_repo,
    check_v2_allow_paths,
    check_v3_cjk_placeholders,
    validate_children,
)
from issuesmith.steps import sub1_create as sub1
from issuesmith.steps.base import StepContext

# --- Real API strings (values unchanged from live capture) ---

ISSUE_CREATE_SUCCESS_JSON = json.dumps(
    {
        "number": 3000,
        "title": "スレッドからの委譲ジョブのキャンセル",
        "state": "CLOSED",
        "html_url": "https://github.com/sumipan/nexus/issues/3000",
        "id": 5401620429,
    },
    ensure_ascii=False,
)

ISSUE_CREATE_FAILURE_MESSAGE = (
    "GitHub API POST https://api.github.com/repos/sumipan/nexus/issues "
    "failed (422): Validation Failed"
)

_ENGINE_STDOUT = {
    "claude": "Body ready.\nPIPELINE_STATUS: SUB_BODY_READY\n",
    "cursor": "Body ready.\n`PIPELINE_STATUS: SUB_BODY_READY`\n",
    "codex": "Body ready.\n**PIPELINE_STATUS: SUB_BODY_READY**\n",
}


def _ctx(**overrides: str) -> StepContext:
    base = {
        "issue_number": "3166",
        "base_branch": "main",
        "handler_name": "sub",
        "is_cross_repo": "true",
        "target_clone_path": ".claude/external/issuesmith",
        "source": "",
        "workflow_name": "issuesmith",
        "m1_result_filename": "",
        "m1r_result_filename": "",
        "target_repo": "sumipan/issuesmith",
        "allow_paths": "- src/**",
    }
    base.update(overrides)
    return StepContext(**base)


def _yaml(target_repo: str, allow_paths: list[str]) -> str:
    paths = "\n".join(f'  - "{p}"' for p in allow_paths)
    return (
        f"```yaml\ntarget_repo: {target_repo}\nbase_branch: main\n"
        f"allow_paths:\n{paths}\n```\n"
    )


def _parent_body(
    *,
    child_repo: str = "sumipan/nexus",
    with_repo_column: bool = True,
    title: str = "child work",
) -> str:
    if with_repo_column:
        plan = (
            "### サブイシュー分割計画\n"
            "| # | タイトル | 対象リポジトリ | 内容 | 依存 |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | {title} | `{child_repo}` | do work | なし |\n"
        )
    else:
        plan = (
            "### サブイシュー分割計画\n"
            "| # | タイトル | 内容 | 依存 |\n"
            "|---|--------|------|------|\n"
            f"| 1 | {title} | do work | なし |\n"
        )
    return (
        _yaml("sumipan/nexus", ["src/**"])
        + "\n## 設計\n\n設計本文です。\n\n"
        "#### サブ1: child\n\n"
        "**スコープ**: do work\n\n"
        "**設計方針**: design detail\n\n"
        "**変更対象ファイル**:\n"
        "| リポジトリ | ファイルパス | 変更種別 | 変更内容 |\n"
        "|---|---|---|---|\n"
        f"| `{child_repo}` | `src/a.py` | 修正 | x |\n\n"
        "```yaml\n"
        "paths_must_exist: []\n"
        "```\n\n"
        "## 受け入れ条件\n\n- [x] ok\n\n"
        "## マイルストーン\n\n"
        + plan
    )


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


def test_issue_create_fixtures_are_real_strings() -> None:
    success = json.loads(ISSUE_CREATE_SUCCESS_JSON)
    assert success["number"] == 3000
    assert success["html_url"].endswith("/issues/3000")
    assert "422" in ISSUE_CREATE_FAILURE_MESSAGE
    assert "Validation Failed" in ISSUE_CREATE_FAILURE_MESSAGE


def test_create_issue_success_returns_number_from_real_shape() -> None:
    client = MagicMock()
    payload = json.loads(ISSUE_CREATE_SUCCESS_JSON)
    client.issue_create.return_value = int(payload["number"])
    number = sub1._create_child_issue(
        client,
        title="t",
        body="b",
        labels=["issuesmith:draft-done"],
        milestone=None,
    )
    assert number == 3000
    client.issue_create.assert_called_once()


def test_create_issue_failure_propagates_real_error_message() -> None:
    client = MagicMock()
    client.issue_create.side_effect = RuntimeError(ISSUE_CREATE_FAILURE_MESSAGE)
    with pytest.raises(RuntimeError, match="422.*Validation Failed"):
        sub1._create_child_issue(client, title="", body="x", labels=None, milestone=None)


@pytest.mark.parametrize("engine", ["claude", "cursor", "codex"])
def test_run_guarded_marker_extraction_stable_across_engines(engine: str) -> None:
    stdout = _ENGINE_STDOUT[engine]
    assert _extract_status_values(stdout) == ["SUB_BODY_READY"]


def test_v1_v2_v3_helpers_used_by_validate_children_and_sub1() -> None:
    """共通ヘルパーが validate_children と sub1_create の双方から呼ばれる。"""
    parent = {
        "number": 100,
        "body": _parent_body(),
        "milestone": {"number": 1},
        "labels": [{"name": "scope:milestone"}],
    }
    child_body = (
        _yaml("sumipan/nexus", ["src/**"])
        + "\n**変更対象ファイル**:\n"
        "| リポジトリ | ファイルパス | 変更種別 | 変更内容 |\n"
        "|---|---|---|---|\n"
        "| `sumipan/nexus` | `src/a.py` | 修正 | x |\n"
    )
    child = {
        "number": 101,
        "title": "child work",
        "body": child_body,
        "milestone": {"number": 1},
        "labels": [{"name": "issuesmith:draft-done"}],
    }
    client = MagicMock()
    client.issue_get.return_value = {"number": 101}

    with (
        patch(
            "issuesmith.milestone.check_v1_target_repo", wraps=check_v1_target_repo
        ) as v1,
        patch(
            "issuesmith.milestone.check_v2_allow_paths", wraps=check_v2_allow_paths
        ) as v2,
        patch(
            "issuesmith.milestone.check_v3_cjk_placeholders",
            wraps=check_v3_cjk_placeholders,
        ) as v3,
    ):
        result = validate_children(parent, [child], client=client)
        assert result.passed is True
        assert v1.called and v2.called and v3.called

    failures = sub1._prevalidate_child_body(
        body=child_body,
        row_repo="sumipan/nexus",
        parent_issue_number=100,
        resolved_dep="なし",
        client=client,
        supported=frozenset({"sumipan/nexus"}),
    )
    assert failures == []

    # Direct helper use from sub1 path (prevalidate calls them)
    with (
        patch.object(sub1, "check_v1_target_repo", wraps=check_v1_target_repo) as sv1,
        patch.object(sub1, "check_v2_allow_paths", wraps=check_v2_allow_paths) as sv2,
        patch.object(
            sub1, "check_v3_cjk_placeholders", wraps=check_v3_cjk_placeholders
        ) as sv3,
    ):
        sub1._prevalidate_child_body(
            body=child_body,
            row_repo="sumipan/nexus",
            parent_issue_number=100,
            resolved_dep="なし",
            client=client,
            supported=frozenset({"sumipan/nexus"}),
        )
        assert sv1.called and sv2.called and sv3.called


def test_parse_plan_table_by_header_names_not_column_order() -> None:
    body = (
        "## マイルストーン\n\n"
        "### サブイシュー分割計画\n"
        "| 依存 | 内容 | タイトル | # | 対象リポジトリ |\n"
        "|------|------|----------|---|----------------|\n"
        "| なし | scope | my title | 2 | `sumipan/ghdag` |\n"
    )
    rows, has_repo = sub1._parse_split_plan(body, parent_target_repo="sumipan/nexus")
    assert has_repo is True
    assert len(rows) == 1
    assert rows[0].row_num == 2
    assert rows[0].title == "my title"
    assert rows[0].repo == "sumipan/ghdag"
    assert rows[0].scope == "scope"
    assert rows[0].dep_raw == "なし"


def test_four_column_plan_falls_back_to_parent_target_repo() -> None:
    body = (
        "## マイルストーン\n\n"
        "### サブイシュー分割計画\n"
        "| # | タイトル | 内容 | 依存 |\n"
        "|---|--------|------|------|\n"
        "| 1 | t | c | なし |\n"
    )
    rows, has_repo = sub1._parse_split_plan(body, parent_target_repo="sumipan/nexus")
    assert has_repo is False
    assert rows[0].repo == "sumipan/nexus"


def test_run_creates_child_and_returns_sub_created() -> None:
    parent_body = _parent_body()
    client = MagicMock()
    client.issue_get.side_effect = [
        {
            "number": 3166,
            "body": parent_body,
            "labels": [{"name": "scope:milestone"}],
            "milestone": {"number": 7},
            "comments": [
                {"body": "PIPELINE_STATUS: BRUSHUP_DONE", "createdAt": "2026-01-01T00:00:00Z"},
                {
                    "body": "CP1_STATUS: PASS\nINTENTIONAL_HOLD: true",
                    "createdAt": "2026-01-01T01:00:00Z",
                },
            ],
        },
        # ensure_sub1_binding / post validate children fetches
        {"number": 3166, "milestone": {"number": 7}},
        {
            "number": 9001,
            "title": "child work",
            "body": "",  # filled after create via validate path
            "milestone": {"number": 7},
            "labels": [{"name": "issuesmith:draft-done"}],
        },
    ]
    client.list_sub_issues = MagicMock(return_value=[])
    client.issue_create.return_value = 9001
    client.add_sub_issue = MagicMock(return_value=None)

    created_bodies: list[str] = []

    def _capture_create(title, body, *, labels=None, milestone=None):
        created_bodies.append(body)
        return 9001

    client.issue_create.side_effect = _capture_create

    with (
        patch.object(sub1, "_github_client", return_value=client),
        patch.object(sub1, "ensure_sub1_binding", return_value=True),
        patch.object(
            sub1,
            "validate_children",
            return_value=MagicMock(passed=True, results=[]),
        ),
        patch.object(sub1, "get_config") as cfg,
    ):
        cfg.return_value.supported_repos = frozenset(
            {"sumipan/nexus", "sumipan/issuesmith", "sumipan/ghdag"}
        )
        cfg.return_value.sections = {
            "sub_plan": "サブイシュー分割計画",
            "milestone": "マイルストーン",
            "design": "設計",
            "changed_files": "変更対象ファイル",
            "dependencies": "依存（先行）",
        }
        result = sub1.run(_ctx())

    assert result.exit_code == 0
    assert result.pipeline_status == "SUB_CREATED"
    assert client.issue_create.called
    assert created_bodies
    assert "target_repo: sumipan/nexus" in created_bodies[0]
    client.issue_update.assert_called()  # draft-done / scope labels


def test_run_auto_creates_milestone_when_unset() -> None:
    """AC-7: SUB1 creates milestone object when parent has none."""
    parent_body = _parent_body()
    parent_no_ms = {
        "number": 3166,
        "body": parent_body,
        "labels": [{"name": "scope:milestone"}],
        "milestone": None,
        "comments": [
            {"body": "PIPELINE_STATUS: BRUSHUP_DONE", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "CP1_STATUS: PASS", "createdAt": "2026-01-01T01:00:00Z"},
        ],
    }
    parent_with_ms = {
        **parent_no_ms,
        "milestone": {"number": 42, "title": "3166-20260913"},
    }

    client = MagicMock()
    # 1) run initial  2) _ensure_milestone check  3) refresh after create
    # 4+) ensure_sub1_binding / validate
    client.issue_get.side_effect = [
        parent_no_ms,
        parent_no_ms,
        parent_with_ms,
        {"number": 3166, "milestone": {"number": 42}},
        {
            "number": 9001,
            "title": "child work",
            "body": "",
            "milestone": {"number": 42},
            "labels": [{"name": "issuesmith:draft-done"}],
        },
    ]
    client.list_sub_issues = MagicMock(return_value=[])
    client.issue_create.return_value = 9001
    client.milestone_list.return_value = []
    client.milestone_create.return_value = 42

    with (
        patch.object(sub1, "_github_client", return_value=client),
        patch.object(sub1, "ensure_sub1_binding", return_value=True),
        patch.object(
            sub1,
            "validate_children",
            return_value=MagicMock(passed=True, results=[]),
        ),
        patch("issuesmith.convert_to_milestone.get_config") as ctm_cfg,
        patch.object(sub1, "get_config") as cfg,
    ):
        ctm_cfg.return_value.timezone = "Asia/Tokyo"
        cfg.return_value.supported_repos = frozenset({"sumipan/nexus"})
        cfg.return_value.sections = {
            "sub_plan": "サブイシュー分割計画",
            "milestone": "マイルストーン",
            "design": "設計",
            "changed_files": "変更対象ファイル",
            "dependencies": "依存（先行）",
        }
        result = sub1.run(_ctx())

    assert result.exit_code == 0
    assert result.pipeline_status == "SUB_CREATED"
    assert client.milestone_create.called
    comment_bodies = [c.args[1] for c in client.issue_comment.call_args_list]
    assert any("milestone を自動作成" in b for b in comment_bodies)


def test_run_all_rows_fail_validation_exits_nonzero() -> None:
    # 5-col plan with empty 対象リポジトリ → row validation failure
    body = (
        _yaml("sumipan/nexus", ["src/**"])
        + "\n## 設計\n\n設計本文です。\n\n"
        "#### サブ1: x\n\n**スコープ**: x\n\n"
        "## 受け入れ条件\n\n- [x] ok\n\n"
        "## マイルストーン\n\n"
        "### サブイシュー分割計画\n"
        "| # | タイトル | 対象リポジトリ | 内容 | 依存 |\n"
        "|---|--------|----------------|------|------|\n"
        "| 1 | bad row |  | do | なし |\n"
    )
    client = MagicMock()
    client.issue_get.return_value = {
        "number": 3166,
        "body": body,
        "labels": [{"name": "scope:milestone"}],
        "milestone": {"number": 1},
        "comments": [
            {"body": "PIPELINE_STATUS: BRUSHUP_DONE", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "CP1_STATUS: PASS", "createdAt": "2026-01-01T01:00:00Z"},
        ],
    }
    client.list_sub_issues = MagicMock(return_value=[])

    with (
        patch.object(sub1, "_github_client", return_value=client),
        patch.object(sub1, "get_config") as cfg,
    ):
        cfg.return_value.supported_repos = frozenset({"sumipan/nexus"})
        cfg.return_value.sections = {
            "sub_plan": "サブイシュー分割計画",
            "milestone": "マイルストーン",
            "design": "設計",
            "changed_files": "変更対象ファイル",
            "dependencies": "依存（先行）",
        }
        result = sub1.run(_ctx())

    assert result.exit_code == 1
    assert result.pipeline_status == "IMPL_FAILED"
    client.issue_create.assert_not_called()
