"""SUB1 guarded-body: errors, timeouts, polish fallback and template variables."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import test_sub1_create as _base
from test_sub1_create import (
    _CHANGE_TABLE_HEADER,
    ACCEPTANCE_CRITERIA,
    CHANGED_FILES,
    CONTENT,
    DEPENDENCY,
    DESIGN,
    NONE,
    TARGET_REPOSITORY,
    TITLE,
    _cfg_mock,
    _ctx,
    _parent_body,
    _parent_issue_dict,
    _yaml,
)
from tests.legacy_text import (
    MODIFY,
    SUB,
)

from issuesmith.config import get_config as _real_get_config
from issuesmith.engine import RoleSelection
from issuesmith.milestone import (
    run_guarded_sub1_body,
    run_sub1_create,
)

_reset_cfg = _base._reset_cfg


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
    with patch("issuesmith.engine.resolve", side_effect=fake_resolve), patch(
        "issuesmith.engine.run_guarded", side_effect=fake_run_guarded
    ):
        rc = run_guarded_sub1_body(
            ctx, row=row, body_path=tmp_path / "b.md", template_name="sub-body.md"
        )
    assert rc == 0
    assert calls["resolve"] == ("implementation", None)
    assert "model=m" in calls["run_guarded"][1]


# AC-1: ValueError in _run_guarded_body → early fail with SUB1_BODY_INIT_ERROR
def test_run_guarded_body_value_error_exits_nonzero(capsys) -> None:
    """AC-1: ValueError (config bug) causes immediate non-zero exit with SUB1_BODY_INIT_ERROR."""
    parent_body = _parent_body()
    client = MagicMock()
    client.issue_get.return_value = _parent_issue_dict(parent_body)
    client.list_sub_issues = MagicMock(return_value=[])

    with (
        patch("issuesmith.milestone.get_forge", return_value=client),
        patch("issuesmith.milestone.resolve_sub1_template", return_value="sub-ready.md"),
        patch("issuesmith.engine.resolve", side_effect=ValueError("tier must be one of: heavy, light")),
        patch("issuesmith.milestone.get_config") as cfg,
    ):
        cfg.return_value.language = _real_get_config().language
        _cfg_mock(cfg)
        with pytest.raises(SystemExit) as exc_info:
            run_sub1_create(_ctx())

    assert exc_info.value.code != 0
    captured = capsys.readouterr()
    assert "SUB1_BODY_INIT_ERROR" in captured.err


# AC-2: TimeoutExpired stops SUB1 for this row and later rows
def test_run_guarded_body_timeout_stops_sub1(capsys) -> None:
    """TimeoutExpired on row 2 polish: row 1 create only once, SUB1_BODY_POLISH_TIMEOUT."""
    two_row_body = (
        _yaml("sumipan/nexus", ["src/**"])
        + f"\n## {DESIGN}\n\nParent design.\n\n"
        f"#### {SUB}1: first row\n\n**Scope**: x\n\n"
        f"**{CHANGED_FILES}**:\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        "| `sumipan/nexus` | `src/a.py` | Modify | x |\n\n"
        f"#### {SUB}2: second row\n\n**Scope**: y\n\n"
        f"**{CHANGED_FILES}**:\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        "| `sumipan/nexus` | `src/b.py` | Modify | y |\n\n"
        "```yaml\npaths_must_exist: []\n```\n\n"
        f"## {ACCEPTANCE_CRITERIA}\n\n- [x] ok\n\n"
        "## Milestone\n\n"
        "### Sub-issue Plan\n"
        f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|--------|----------------|------|------|\n"
        f"| 1 | first row | `sumipan/nexus` | do | {NONE} |\n"
        f"| 2 | second row | `sumipan/nexus` | do | {NONE} |\n"
    )
    client = MagicMock()
    parent_dict = _parent_issue_dict(two_row_body)

    def _issue_get(num, **kwargs):
        if num == 3166:
            return parent_dict
        if num == 9001:
            return {
                "number": 9001,
                "title": "first row",
                "body": "",
                "milestone": {"number": 7},
                "labels": [{"name": "issuesmith:draft-done"}],
            }
        return {"number": num, "labels": []}

    client.issue_get.side_effect = _issue_get
    client.list_sub_issues = MagicMock(return_value=[])
    client.issue_create.return_value = 9001

    def _guarded_timeout(ctx, *, row, body_path, template_name):
        if row.row_num == 2:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=30)
        return 0

    with (
        patch("issuesmith.milestone.get_forge", return_value=client),
        patch("issuesmith.milestone.resolve_sub1_template", return_value="sub-ready.md"),
        patch("issuesmith.milestone.run_guarded_sub1_body", side_effect=_guarded_timeout),
        patch("issuesmith.milestone.ensure_sub1_binding", return_value=True),
        patch("issuesmith.milestone.get_config") as cfg,
    ):
        cfg.return_value.language = _real_get_config().language
        _cfg_mock(cfg)
        result = run_sub1_create(_ctx())

    captured = capsys.readouterr()
    assert result.pipeline_status == "SUB1_BODY_POLISH_TIMEOUT"
    assert "PIPELINE_STATUS: SUB1_BODY_POLISH_TIMEOUT" in captured.err
    assert "30" in captured.err
    assert client.issue_create.call_count == 1


def test_run_guarded_body_timeout_on_first_row_never_creates(capsys) -> None:
    parent_body = _parent_body()
    client = MagicMock()
    client.issue_get.return_value = _parent_issue_dict(parent_body)
    client.list_sub_issues = MagicMock(return_value=[])

    with (
        patch("issuesmith.milestone.get_forge", return_value=client),
        patch("issuesmith.milestone.resolve_sub1_template", return_value="sub-ready.md"),
        patch(
            "issuesmith.milestone.run_guarded_sub1_body",
            side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=45),
        ),
        patch("issuesmith.milestone.get_config") as cfg,
    ):
        cfg.return_value.language = _real_get_config().language
        _cfg_mock(cfg)
        result = run_sub1_create(_ctx())

    captured = capsys.readouterr()
    assert result.pipeline_status == "SUB1_BODY_POLISH_TIMEOUT"
    assert client.issue_create.call_count == 0
    assert "45" in captured.err


def _parent_body_with_issue_dep() -> str:
    plan = (
        "### Sub-issue Plan\n"
        f"| # | {TITLE} | {TARGET_REPOSITORY} | {CONTENT} | {DEPENDENCY} |\n"
        "|---|--------|----------------|------|------|\n"
        f"| 1 | child work | `sumipan/nexus` | do work | #4886 |\n"
    )
    return (
        _yaml("sumipan/nexus", ["src/**"])
        + f"\n## {DESIGN}\n\nParent design body.\n\n"
        f"#### {SUB}1: child\n\n"
        "**Scope**: do work\n\n"
        f"**{CHANGED_FILES}**:\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        f"| `sumipan/nexus` | `src/a.py` | {MODIFY} | x |\n\n"
        f"## {ACCEPTANCE_CRITERIA}\n\n- [x] ok\n\n"
        "## Milestone\n\n"
        + plan
    )


def test_polish_v6_failure_falls_back_to_draft_body(capsys) -> None:
    """Polish strips #NNNN from deps table; draft passes V6 and is used for create."""
    parent_body = _parent_body_with_issue_dep()
    dep_issue = {
        "title": "prior work",
        "state": "OPEN",
        "body": "```yaml\ntarget_repo: sumipan/nexus\n```",
        "labels": [],
    }
    client = MagicMock()

    def _issue_get(num, **kwargs):
        if num == 4886:
            return dep_issue
        if num == 3166:
            return _parent_issue_dict(parent_body)
        if num == 9002:
            return {
                "number": 9002,
                "title": "child work",
                "body": "",
                "milestone": {"number": 7},
                "labels": [{"name": "issuesmith:draft-done"}],
            }
        return {"number": num, "labels": []}

    client.issue_get.side_effect = _issue_get
    client.list_sub_issues = MagicMock(return_value=[])
    client.issue_create.return_value = 9002

    def _guarded_strip_issue_refs(ctx, *, row, body_path, template_name):
        text = body_path.read_text(encoding="utf-8")
        broken = text.replace("#4886", "prior work")
        body_path.write_text(broken, encoding="utf-8")
        return 0

    with (
        patch("issuesmith.milestone.get_forge", return_value=client),
        patch("issuesmith.milestone.resolve_sub1_template", return_value="sub-ready.md"),
        patch(
            "issuesmith.milestone.run_guarded_sub1_body",
            side_effect=_guarded_strip_issue_refs,
        ),
        patch("issuesmith.milestone.ensure_sub1_binding", return_value=True),
        patch(
            "issuesmith.milestone.validate_children",
            return_value=MagicMock(passed=True, results=[]),
        ),
        patch("issuesmith.milestone.get_config") as cfg,
    ):
        cfg.return_value.language = _real_get_config().language
        _cfg_mock(cfg)
        run_sub1_create(_ctx())

    captured = capsys.readouterr()
    assert "falling back to draft" in captured.err
    create_body = client.issue_create.call_args[0][1]
    assert "#4886" in create_body


# AC-3: All rows fail with CalledProcessError → non-zero exit
def test_run_all_rows_guarded_body_fail_exits_nonzero(capsys) -> None:
    """AC-3: All rows fail with CalledProcessError → skip_count == total_rows → non-zero exit."""
    parent_body = _parent_body()
    client = MagicMock()
    client.issue_get.return_value = _parent_issue_dict(parent_body)
    client.list_sub_issues = MagicMock(return_value=[])

    with (
        patch("issuesmith.milestone.get_forge", return_value=client),
        patch("issuesmith.milestone.resolve_sub1_template", return_value="sub-ready.md"),
        patch(
            "issuesmith.milestone.run_guarded_sub1_body",
            side_effect=subprocess.CalledProcessError(returncode=1, cmd="claude"),
        ),
        patch("issuesmith.milestone.get_config") as cfg,
    ):
        cfg.return_value.language = _real_get_config().language
        _cfg_mock(cfg)
        with pytest.raises(SystemExit) as exc_info:
            run_sub1_create(_ctx())

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
        patch("issuesmith.engine.resolve", side_effect=fake_resolve),
        patch("issuesmith.engine.run_guarded", side_effect=fake_run_guarded),
    ):
        rc = run_guarded_sub1_body(
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
        # get_identifiers() is Python 3.11+; use pattern regex for 3.10 compat
        identifiers = {
            m.group("named") or m.group("braced")
            for m in string.Template.pattern.finditer(template_content)
            if m.group("named") or m.group("braced")
        }
        missing = sorted(identifiers - set(var_dict))
        assert missing == [], f"Undefined variables: {missing}"
        return 0

    ctx = MagicMock(
        issue_number="3445",
        target_repo="sumipan/issuesmith",
        execution_constraints="",
    )
    row = MagicMock(row_num=1, title="t", repo="sumipan/issuesmith")
    with (
        patch("issuesmith.engine.resolve", side_effect=fake_resolve),
        patch("issuesmith.engine.run_guarded", side_effect=fake_run_guarded),
        patch("issuesmith.milestone.get_config") as cfg,
    ):
        cfg.return_value.language = _real_get_config().language
        cfg.return_value.paths.template_dir = tmp_path
        rc = run_guarded_sub1_body(
            ctx, row=row, body_path=tmp_path / "b.md", template_name="sub-ready.md"
        )

    assert rc == 0
