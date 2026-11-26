"""Tests for andon auto-answer rules and rule_id round-trip (#4792)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import (
    Andon,
    apply_auto_answers,
    plan_auto_answers,
    to_comment,
)
from issuesmith.config import AutoAnswerRule, ConfigError, load_config


def _rule(**kwargs) -> AutoAnswerRule:
    defaults = {
        "name": "cp2_review_fail",
        "match": {"step": "cp2", "kind": "decision"},
        "action": "resume_from_p1",
        "max_per_issue": 1,
    }
    defaults.update(kwargs)
    return AutoAnswerRule(**defaults)


def _client_with_andons(andons: list[Andon], comments_by_issue: dict[int, list[str]] | None = None):
    client = MagicMock()
    comments_by_issue = comments_by_issue or {}

    def _comments(issue: int):
        bodies = []
        for a in andons:
            if a.issue == issue:
                bodies.append({"body": to_comment(a)})
        for body in comments_by_issue.get(issue, []):
            bodies.append({"body": body})
        return bodies

    client.get_issue_comments.side_effect = lambda issue: _comments(issue)
    client.list_issues.return_value = [{"number": a.issue} for a in andons]
    client.issue_comment.return_value = {}
    return client


def test_match_by_step_kind_not_summary():
    andon = Andon(
        id="wf:42:cp2:0",
        kind="decision",
        issue=42,
        step="cp2",
        summary="CP2 review FAIL",
    )
    wrong = Andon(
        id="wf:42:p3:0",
        kind="decision",
        issue=42,
        step="p3",
        summary="CP2 review FAIL",
    )
    rules = (_rule(),)
    client = _client_with_andons([andon, wrong])
    plan = plan_auto_answers(client, rules)
    assert len(plan.answers) == 1
    assert plan.answers[0]["andon_id"] == andon.id
    assert len(plan.escalations) == 1
    assert plan.escalations[0]["kind"] == "decision"


def test_rule_id_prefix_match():
    andon = Andon(
        id="wf:1:cp2:0",
        kind="decision",
        issue=1,
        step="cp2",
        summary="",
        rule_id="cp2.review.fail",
    )
    rules = (_rule(name="rid", match={"rule_id": "cp2.review"}, action="resume"),)
    client = _client_with_andons([andon])
    plan = plan_auto_answers(client, rules)
    assert len(plan.answers) == 1

    no_match = Andon(
        id="wf:2:cp2:0",
        kind="decision",
        issue=2,
        step="cp2",
        summary="",
        rule_id="cp2.reviewer",
    )
    client2 = _client_with_andons([no_match])
    plan2 = plan_auto_answers(client2, rules)
    assert plan2.answers == []
    assert plan2.escalations[0]["kind"] == "decision"


def test_max_per_issue_counts_all_comments(tmp_path, monkeypatch):
    note_body = (
        "<!-- andon-note -->\n"
        "```andon-note\n"
        "id: wf:42:cp2:0\n"
        "key: auto_answer\n"
        "value: cp2_review_fail\n"
        "```\n"
    )
    andon = Andon(id="wf:42:cp2:0", kind="decision", issue=42, step="cp2", summary="again")
    client = _client_with_andons([andon], comments_by_issue={42: [note_body]})
    plan = plan_auto_answers(client, (_rule(),))
    assert plan.answers == []
    assert plan.escalations[0]["kind"] == "auto_answer_exhausted"


@patch("issuesmith.andon.resume")
@patch("issuesmith.andon.answer")
def test_apply_writes_note_before_answer(mock_answer, mock_resume):
    andon = Andon(id="wf:42:cp2:0", kind="decision", issue=42, step="cp2", summary="s")
    client = _client_with_andons([andon])
    plan = plan_auto_answers(client, (_rule(),))
    calls: list[str] = []

    def _comment(issue, body):
        calls.append("note" if "andon-note" in body else "other")

    client.issue_comment.side_effect = _comment
    apply_auto_answers(client, plan)
    assert calls[0] == "note"
    mock_answer.assert_called_once()


@patch("issuesmith.andon.resume")
def test_resume_from_p1_calls_force(mock_resume):
    from issuesmith.andon import _call_resume_hook

    _call_resume_hook(MagicMock(), "wf:42:cp2:0", "resume_from_p1")
    mock_resume.assert_called_once_with(42, from_step="p1", force=True)


def test_active_issues_excluded_from_auto_answers():
    """Issue #7326: andons on DAG-active issues are skipped for auto-answer."""
    andon = Andon(
        id="wf:99:cp2:0",
        kind="decision",
        issue=99,
        step="cp2",
        summary="needs human",
    )
    rules = (_rule(),)
    client = _client_with_andons([andon])
    plan = plan_auto_answers(client, rules, active_issues={99})
    assert plan.answers == []
    assert plan.escalations == []

    plan_open = plan_auto_answers(client, rules)
    assert len(plan_open.answers) == 1
    assert plan_open.answers[0]["issue"] == 99


def test_config_auto_answer_validation(tmp_path, monkeypatch):
    path = tmp_path / "issuesmith.yaml"
    path.write_text(
        "repo: org/repo\n"
        "andon:\n"
        "  auto_answer:\n"
        "    - name: a\n"
        "      match: {}\n"
        "      action: x\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    with pytest.raises(ConfigError):
        load_config(path)


def test_andon_causes_config_override(tmp_path, monkeypatch):
    """Refs #8075 AC-7."""
    from issuesmith.andon import Andon, classify_cause
    from issuesmith.config import reset_config_cache

    path = tmp_path / "issuesmith.yaml"
    path.write_text(
        "repo: org/repo\n"
        "andon:\n"
        "  causes:\n"
        "    hostgate.flaky: transient\n"
        "    tests.interrupted: issue_design\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    reset_config_cache()
    cfg = load_config(path)
    andon = Andon(
        id="wf:1:p1:0",
        kind="blocked",
        issue=1,
        step="p1",
        summary="",
        rule_id="hostgate.flaky.x",
    )
    assert cfg.andon.causes["hostgate.flaky"] == "transient"
    assert classify_cause(andon) == "transient"
    overridden = Andon(
        id="wf:2:p1:0",
        kind="blocked",
        issue=2,
        step="p1",
        summary="",
        rule_id="tests.interrupted",
    )
    assert classify_cause(overridden) == "issue_design"
    reset_config_cache()


@pytest.mark.parametrize(
    "andon_yaml,msg",
    [
        ("  causes:\n    x: not_a_cause\n", "andon.causes"),
        ("  auto_rerun_max: -1\n", "auto_rerun_max"),
        ("  auto_rerun_max: true\n", "auto_rerun_max"),
    ],
)
def test_andon_config_validation(tmp_path, monkeypatch, andon_yaml, msg):
    """Refs #8075 AC-7."""
    path = tmp_path / "issuesmith.yaml"
    path.write_text(f"repo: org/repo\nandon:\n{andon_yaml}", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    with pytest.raises(ConfigError, match=msg):
        load_config(path)
