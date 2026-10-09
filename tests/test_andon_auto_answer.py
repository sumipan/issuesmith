"""Tests for andon auto-answer rules and rule_id round-trip (#4792)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import (
    Andon,
    apply_auto_answers,
    from_comment,
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


def test_rule_id_round_trip():
    a = Andon(
        id="wf:1:cp2:0",
        kind="decision",
        issue=1,
        step="cp2",
        summary="x",
        rule_id="cp2.review.fail",
    )
    parsed = from_comment(to_comment(a))
    assert parsed is not None
    assert parsed.rule_id == "cp2.review.fail"


def test_old_comment_missing_rule_id():
    text = (
        "```andon\n"
        "id: wf:1:cp2:0\n"
        "kind: decision\n"
        "issue: 1\n"
        "step: cp2\n"
        "summary: old\n"
        "```\n"
    )
    parsed = from_comment(text)
    assert parsed is not None
    assert parsed.rule_id == ""


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
