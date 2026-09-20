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
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from tests.legacy_text import (
    ACCEPTANCE_CRITERIA,
    CHANGE_TYPE,
    CHANGED_FILES,
    CONTENT,
    DEPENDENCY,
    DESCRIPTION,
    DESIGN,
    FILE_PATH,
    MODIFY,
    NONE,
    REPOSITORY,
    SUB,
    TARGET_REPOSITORY,
    TITLE,
)

from issuesmith.config import reset_config_cache
from issuesmith.engine import RoleSelection, _extract_status_values
from issuesmith.milestone import (
    check_v1_target_repo,
    check_v2_allow_paths,
    check_v3_cjk_placeholders,
    validate_children,
)
from issuesmith.steps import sub1_create as sub1
from issuesmith.steps.base import StepContext

_CHANGE_TABLE_HEADER = f"{REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION}"

# --- Real API strings (values unchanged from live capture) ---

ISSUE_CREATE_SUCCESS_JSON = json.dumps(
    {
        "number": 3000,
        "title": "Cancel thread delegation job",
        "state": "CLOSED",
        "html_url": "https://github.com/sumipan/nexus/issues/3000",
        "id": 5401620429,
    },
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


# ASCII fixture data.
def _parent_body(
    *,
    child_repo: str = "sumipan/nexus",
    with_repo_column: bool = True,
    title: str = "child work",
) -> str:
    if with_repo_column:
        plan = (
            "### Sub-issue Plan\n"
            f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|----------------|------|------|\n"
            f"| 1 | {title} | `{child_repo}` | do work | {NONE} |\n"
        )
    else:
        plan = (
            "### Sub-issue Plan\n"
            f"| # | {TITLE} | {CONTENT} | {DEPENDENCY} |\n"
            "|---|--------|------|------|\n"
            f"| 1 | {title} | do work | {NONE} |\n"
        )
    return (
        _yaml("sumipan/nexus", ["src/**"])
        + f"\n## {DESIGN}\n\nParent design body.\n\n"
        f"#### {SUB}1: child\n\n"
        "**Scope**: do work\n\n"
        "**Design Policy**: design detail\n\n"
        f"**{CHANGED_FILES}**:\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        f"| `{child_repo}` | `src/a.py` | {MODIFY} | x |\n\n"
        "```yaml\n"
        "paths_must_exist: []\n"
        "```\n\n"
        f"## {ACCEPTANCE_CRITERIA}\n\n- [x] ok\n\n"
        "## Milestone\n\n"
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
    """Shared helpers are called from both validate_children and sub1_create."""
    parent = {
        "number": 100,
        "body": _parent_body(),
        "milestone": {"number": 1},
        "labels": [{"name": "scope:milestone"}],
    }
    # ASCII fixture data.
    child_body = (
        _yaml("sumipan/nexus", ["src/**"])
        + "\n**Changed Files**:\n"
            f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
            f"| `sumipan/nexus` | `src/a.py` | {MODIFY} | x |\n"
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

    # ASCII fixture data.
    failures = sub1._prevalidate_child_body(
        body=child_body,
        row_repo="sumipan/nexus",
        parent_issue_number=100,
        resolved_dep=NONE,
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
            resolved_dep=NONE,
            client=client,
            supported=frozenset({"sumipan/nexus"}),
        )
        assert sv1.called and sv2.called and sv3.called


def test_parse_plan_table_by_header_names_not_column_order() -> None:
    # ASCII fixture data.
    body = (
        "## Milestone\n\n"
        "### Sub-issue Plan\n"
        f"| {DEPENDENCY} | {CONTENT} | {TITLE} | # | {TARGET_REPOSITORY} |\n"
        "|------|------|----------|---|----------------|\n"
        f"| {NONE} | scope | my title | 2 | `sumipan/ghdag` |\n"
    )
    rows, has_repo = sub1._parse_split_plan(body, parent_target_repo="sumipan/nexus")
    assert has_repo is True
    assert len(rows) == 1
    assert rows[0].row_num == 2
    assert rows[0].title == "my title"
    assert rows[0].repo == "sumipan/ghdag"
    assert rows[0].scope == "scope"
    assert rows[0].dep_raw == NONE


def test_four_column_plan_falls_back_to_parent_target_repo() -> None:
    # ASCII fixture data.
    body = (
        "## Milestone\n\n"
        "### Sub-issue Plan\n"
        f"| # | {TITLE} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|--------|------|------|\n"
        f"| 1 | t | c | {NONE} |\n"
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
        patch.object(sub1, "_resolve_template", return_value=None),
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
        # ASCII fixture data.
        cfg.return_value.sections = {
            "sub_plan": "Sub-issue Plan",
            "milestone": "Milestone",
            "design": "Design",
            "changed_files": "Changed Files",
            "dependencies": "Dependencies",
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
        patch.object(sub1, "_resolve_template", return_value=None),
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
        # ASCII fixture data.
        cfg.return_value.sections = {
            "sub_plan": "Sub-issue Plan",
            "milestone": "Milestone",
            "design": "Design",
            "changed_files": "Changed Files",
            "dependencies": "Dependencies",
        }
        result = sub1.run(_ctx())

    assert result.exit_code == 0
    assert result.pipeline_status == "SUB_CREATED"
    assert client.milestone_create.called
    comment_bodies = [c.args[1] for c in client.issue_comment.call_args_list]
    assert any("milestone" in b and "#42" in b for b in comment_bodies)


def test_run_all_rows_fail_validation_exits_nonzero() -> None:
    # 5-col plan with empty target-repo column → row validation failure
    # ASCII fixture data.
    body = (
        _yaml("sumipan/nexus", ["src/**"])
        + "\n## Design\n\nDesign_c672C_c6587_c3067_c3059_c3002\n\n"
        f"#### {SUB}1: x\n\n**Scope**: x\n\n"
        "## Acceptance Criteria\n\n- [x] ok\n\n"
        "## Milestone\n\n"
        "### Sub-issue Plan\n"
        f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|--------|----------------|------|------|\n"
        f"| 1 | bad row |  | do | {NONE} |\n"
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
        # ASCII fixture data.
        cfg.return_value.sections = {
            "sub_plan": "Sub-issue Plan",
            "milestone": "Milestone",
            "design": "Design",
            "changed_files": "Changed Files",
            "dependencies": "Dependencies",
        }
        result = sub1.run(_ctx())

    assert result.exit_code == 1
    assert result.pipeline_status == "IMPL_FAILED"
    client.issue_create.assert_not_called()


def test_resolve_dependencies_replaces_refs_token_wise() -> None:
    """Regression: after "#2" -> "#3382", "#3" must not match inside "#3382" (was "#3383382")."""
    state = sub1.Sub1State()
    client = MagicMock()
    out = sub1._resolve_dependencies(
        "#2, #3",
        table_row_count=8,
        row_to_issue={2: 3382, 3: 3383},
        client=client,
        state=state,
    )
    assert out == "#3382, #3383"
    assert state.unresolved_forward_logs == []
    client.issue_get.assert_not_called()


def test_resolve_dependencies_keeps_forward_ref_and_drops_milestone() -> None:
    state = sub1.Sub1State()
    client = MagicMock()
    client.issue_get.return_value = {"labels": [{"name": "scope:milestone"}]}
    out = sub1._resolve_dependencies(
        "#1, #7, #3301",
        table_row_count=8,
        row_to_issue={1: 3381},
        client=client,
        state=state,
    )
    assert out == "#3381, #7"
    assert len(state.unresolved_forward_logs) == 1 and state.unresolved_forward_logs[0].endswith("7")
    assert len(state.excluded_milestone_logs) == 1 and state.excluded_milestone_logs[0].endswith("#3301")


def test_run_guarded_body_does_not_pass_invalid_tier(tmp_path) -> None:
    """resolve(role, "default") raised ValueError (not in TIERS) so body generation was always skipped."""
    calls: dict[str, object] = {}

    def fake_resolve(role: str, tier: str | None = None):
        calls["resolve"] = (role, tier)
        return RoleSelection(engine="claude", model="m")

    def fake_run_guarded(role, template, variables, **kw):
        calls["run_guarded"] = (role, variables)
        return 0

    ctx = MagicMock(issue_number="3379", target_repo="sumipan/nexus")
    row = MagicMock(row_num=1, title="t", repo="sumipan/nexus")
    with patch.object(sub1, "resolve", side_effect=fake_resolve), patch.object(
        sub1, "run_guarded", side_effect=fake_run_guarded
    ):
        rc = sub1._run_guarded_body(ctx, row=row, body_path=tmp_path / "b.md", template_name="sub-body.md")
    assert rc == 0
    assert calls["resolve"] == ("implementation", None)
    assert "model=m" in calls["run_guarded"][1]


def _parent_issue_dict(body: str) -> dict:
    return {
        "number": 3166,
        "body": body,
        "labels": [{"name": "scope:milestone"}],
        "milestone": {"number": 7},
        "comments": [
            {"body": "PIPELINE_STATUS: BRUSHUP_DONE", "createdAt": "2026-01-01T00:00:00Z"},
            {"body": "CP1_STATUS: PASS", "createdAt": "2026-01-01T01:00:00Z"},
        ],
    }


def _cfg_mock(cfg: MagicMock) -> None:
    cfg.return_value.supported_repos = frozenset({"sumipan/nexus"})
    cfg.return_value.sections = {
        "sub_plan": "Sub-issue Plan",
        "milestone": "Milestone",
        "design": "Design",
        "changed_files": "Changed Files",
        "dependencies": "Dependencies",
    }
    cfg.return_value.paths.template_dir = Path("/tmp/tmpl")


# AC-1: ValueError in _run_guarded_body → early fail with SUB1_BODY_INIT_ERROR
def test_run_guarded_body_value_error_exits_nonzero(capsys) -> None:
    """AC-1: ValueError (config bug) causes immediate non-zero exit with SUB1_BODY_INIT_ERROR."""
    parent_body = _parent_body()
    client = MagicMock()
    client.issue_get.return_value = _parent_issue_dict(parent_body)
    client.list_sub_issues = MagicMock(return_value=[])

    with (
        patch.object(sub1, "_github_client", return_value=client),
        patch.object(sub1, "_resolve_template", return_value="sub-ready.md"),
        patch.object(sub1, "resolve", side_effect=ValueError("tier must be one of: heavy, light")),
        patch.object(sub1, "get_config") as cfg,
    ):
        _cfg_mock(cfg)
        with pytest.raises(SystemExit) as exc_info:
            sub1.run(_ctx())

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "SUB1_BODY_INIT_ERROR" in captured.err


# AC-2: TimeoutExpired in _run_guarded_body → WARN + fallback body, processing continues
def test_run_guarded_body_timeout_warns_and_continues(capsys) -> None:
    """AC-2: TimeoutExpired is caught as WARN; fallback body used; loop continues for remaining rows.

    Uses 2 plan rows with 1 existing so that skip_count (1) < table_row_count (2) and the
    all-skip check does not fire, confirming that TimeoutExpired does NOT cause early exit.
    """
    # 2-row plan: "existing row" (already has issue #8000) + "new row" (template fails)
    two_row_body = (
        _yaml("sumipan/nexus", ["src/**"])
        + f"\n## {DESIGN}\n\nParent design.\n\n"
        f"#### {SUB}1: existing row\n\n**Scope**: x\n\n"
        f"#### {SUB}2: new row\n\n**Scope**: y\n\n"
        f"**{CHANGED_FILES}**:\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        "| `sumipan/nexus` | `src/a.py` | Modify | x |\n\n"
        "```yaml\npaths_must_exist: []\n```\n\n"
        f"## {ACCEPTANCE_CRITERIA}\n\n- [x] ok\n\n"
        "## Milestone\n\n"
        "### Sub-issue Plan\n"
        f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|--------|----------------|------|------|\n"
        f"| 1 | existing row | `sumipan/nexus` | do | {NONE} |\n"
        f"| 2 | new row | `sumipan/nexus` | do | {NONE} |\n"
    )
    client = MagicMock()
    client.issue_get.side_effect = [
        _parent_issue_dict(two_row_body),
        # ensure_sub1_binding for existing row
        {"number": 3166, "milestone": {"number": 7}},
        # ensure_sub1_binding for new row + validate_children
        {"number": 3166, "milestone": {"number": 7}},
        {
            "number": 9001,
            "title": "new row",
            "body": "",
            "milestone": {"number": 7},
            "labels": [{"name": "issuesmith:draft-done"}],
        },
    ]
    # Row 1 ("existing row") is already in the chain
    client.list_sub_issues = MagicMock(return_value=[{"number": 8000, "title": "existing row"}])
    client.issue_create.return_value = 9001

    with (
        patch.object(sub1, "_github_client", return_value=client),
        patch.object(sub1, "_resolve_template", return_value="sub-ready.md"),
        patch.object(
            sub1,
            "_run_guarded_body",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30),
        ),
        patch.object(sub1, "ensure_sub1_binding", return_value=True),
        patch.object(sub1, "validate_children", return_value=MagicMock(passed=True, results=[])),
        patch.object(sub1, "get_config") as cfg,
    ):
        _cfg_mock(cfg)
        sub1.run(_ctx())

    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert client.issue_create.called


# AC-3: All rows fail with CalledProcessError → non-zero exit
def test_run_all_rows_guarded_body_fail_exits_nonzero(capsys) -> None:
    """AC-3: All rows fail with CalledProcessError → skip_count == total_rows → non-zero exit."""
    parent_body = _parent_body()
    client = MagicMock()
    client.issue_get.return_value = _parent_issue_dict(parent_body)
    client.list_sub_issues = MagicMock(return_value=[])

    with (
        patch.object(sub1, "_github_client", return_value=client),
        patch.object(sub1, "_resolve_template", return_value="sub-ready.md"),
        patch.object(
            sub1,
            "_run_guarded_body",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd="claude"),
        ),
        patch.object(sub1, "get_config") as cfg,
    ):
        _cfg_mock(cfg)
        with pytest.raises(SystemExit) as exc_info:
            sub1.run(_ctx())

    assert exc_info.value.code != 0


def test_run_guarded_body_passes_execution_constraints_in_variables() -> None:
    """AC-4: _run_guarded_body includes execution_constraints in variables passed to run_guarded."""
    captured: dict[str, object] = {}

    def fake_resolve(role, tier=None):
        return MagicMock(model="m")

    def fake_run_guarded(role, template, variables, **kw):
        captured["variables"] = variables
        return 0

    ctx = MagicMock(
        issue_number="3445",
        target_repo="sumipan/issuesmith",
        execution_constraints="(non-interactive)",
    )
    row = MagicMock(row_num=1, title="t", repo="sumipan/issuesmith")
    with (
        patch.object(sub1, "resolve", side_effect=fake_resolve),
        patch.object(sub1, "run_guarded", side_effect=fake_run_guarded),
    ):
        rc = sub1._run_guarded_body(
            ctx, row=row, body_path=Path("/tmp/b.md"), template_name="sub-ready.md"
        )

    assert rc == 0
    assert any("execution_constraints=(non-interactive)" in v for v in captured["variables"])


def test_run_guarded_body_template_expansion_does_not_raise_on_execution_constraints(
    tmp_path,
) -> None:
    """AC-5: template with ${execution_constraints} expands without raising undefined-variable error."""
    import string

    template_content = (
        "issue_number=${issue_number}\n"
        "execution_constraints=${execution_constraints}\n"
    )
    tmpl_file = tmp_path / "sub-ready.md"
    tmpl_file.write_text(template_content, encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_resolve(role, tier=None):
        return MagicMock(model="m")

    def fake_run_guarded(role, template, variables, **kw):
        captured["variables"] = variables
        var_dict = {}
        for v in variables:
            k, _, val = v.partition("=")
            var_dict[k] = val
        tmpl = string.Template(template_content)
        missing = sorted(set(tmpl.get_identifiers()) - set(var_dict))
        assert missing == [], f"Undefined variables: {missing}"
        return 0

    ctx = MagicMock(
        issue_number="3445",
        target_repo="sumipan/issuesmith",
        execution_constraints="",
    )
    row = MagicMock(row_num=1, title="t", repo="sumipan/issuesmith")
    with (
        patch.object(sub1, "resolve", side_effect=fake_resolve),
        patch.object(sub1, "run_guarded", side_effect=fake_run_guarded),
        patch.object(sub1, "get_config") as cfg,
    ):
        cfg.return_value.paths.template_dir = tmp_path
        rc = sub1._run_guarded_body(
            ctx, row=row, body_path=tmp_path / "b.md", template_name="sub-ready.md"
        )

    assert rc == 0
