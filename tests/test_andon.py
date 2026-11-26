"""Unit tests for issuesmith.andon — Andon dataclass, serialize/parse, raise/list/answer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import (
    Andon,
    answer,
    from_comment,
    list_open,
    raise_andon,
    to_comment,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_andon(
    *,
    andon_id: str = "myflow:42:cp2:0",
    kind: str = "decision",
    issue: int = 42,
    step: str = "cp2",
    summary: str = "Should we proceed?",
    evidence: str = "test evidence",
    options: list[str] | None = None,
    default: str = "yes",
    mention: str = "@sumipan",
) -> Andon:
    return Andon(
        id=andon_id,
        kind=kind,
        issue=issue,
        step=step,
        summary=summary,
        evidence=evidence,
        options=options if options is not None else ["yes", "no"],
        default=default,
        mention=mention,
    )


class RecordingForgeClient:
    """Records forge issue_comment / issue_update calls for behaviour assertions."""

    def __init__(
        self,
        *,
        comments: list[dict] | None = None,
        issues: list[dict] | None = None,
        labels: list | None = None,
    ) -> None:
        self.issue_comments: list[tuple[int, str]] = []
        self.issue_updates: list[tuple[int, dict]] = []
        self.get_issue_comments_calls: list[int] = []
        self._stored_comments = comments or []
        self._issues = issues or []
        self.issue_get = MagicMock(return_value={"labels": labels or []})

    def issue_comment(self, issue: int, body: str) -> dict:
        self.issue_comments.append((issue, body))
        return {"id": 999, "body": body}

    def get_issue_comments(self, issue: int) -> list[dict]:
        self.get_issue_comments_calls.append(issue)
        return self._stored_comments

    def list_issues(self, **kwargs):
        return self._issues

    def issue_update(self, issue: int, **kwargs) -> dict:
        self.issue_updates.append((issue, kwargs))
        return {}


class RecordingSink:
    def __init__(self) -> None:
        self.emitted: list[Andon] = []

    def emit(self, andon: Andon) -> None:
        self.emitted.append(andon)


def _fake_client(
    *,
    comments: list[dict] | None = None,
    issues: list[dict] | None = None,
    labels: list | None = None,
) -> RecordingForgeClient:
    return RecordingForgeClient(comments=comments, issues=issues, labels=labels)


# ---------------------------------------------------------------------------
# Andon dataclass — basic construction
# ---------------------------------------------------------------------------

def test_andon_kind_blocked():
    a = _make_andon(kind="blocked")
    assert a.kind == "blocked"


def test_andon_kind_broken():
    a = _make_andon(kind="broken")
    assert a.kind == "broken"


# ---------------------------------------------------------------------------
# Serialize / parse (to_comment / from_comment)
# ---------------------------------------------------------------------------

def test_to_comment_returns_string():
    a = _make_andon()
    body = to_comment(a)
    assert isinstance(body, str)


def test_to_comment_contains_andon_fence():
    a = _make_andon()
    body = to_comment(a)
    assert "```andon" in body


def test_to_comment_contains_id():
    a = _make_andon()
    body = to_comment(a)
    assert "myflow:42:cp2:0" in body


def test_to_comment_contains_kind():
    a = _make_andon()
    body = to_comment(a)
    assert "decision" in body


def test_to_comment_omits_empty_namespace():
    a = _make_andon()
    assert "namespace:" not in to_comment(a)


def test_from_comment_without_namespace_defaults_empty():
    legacy = to_comment(_make_andon())
    parsed = from_comment(legacy)
    assert parsed is not None
    assert parsed.namespace == ""


def test_from_comment_no_andon_block():
    assert from_comment("just a regular comment") is None


def test_from_comment_empty_body():
    assert from_comment("") is None


def test_from_comment_other_fence():
    assert from_comment("```yaml\nfoo: bar\n```") is None


def test_round_trip_options_empty():
    a = _make_andon(options=[])
    body = to_comment(a)
    parsed = from_comment(body)
    assert parsed is not None
    assert parsed.options == []


_FENCED_TEXT = "Review output:\n```python\nx = 1\n\ny = 2\n```\nDone"


def test_from_comment_round_trips_fenced_summary_and_evidence():
    """refs #7639 AC-1."""
    a = _make_andon(summary=_FENCED_TEXT, evidence=_FENCED_TEXT)
    parsed = from_comment(to_comment(a))
    assert parsed is not None
    assert parsed.id == a.id
    assert parsed.summary == a.summary
    assert parsed.evidence == a.evidence


# ---------------------------------------------------------------------------
# raise_andon
# ---------------------------------------------------------------------------

def _ns() -> str:
    from issuesmith.config import get_config
    return get_config().label_namespace


# ---------------------------------------------------------------------------
# list_open
# ---------------------------------------------------------------------------

def test_list_open_returns_empty_when_no_issues():
    client = _fake_client(issues=[])
    with patch("issuesmith.andon._andon_label_kinds", return_value=["decision", "blocked", "broken"]):
        result = list_open(client)
    assert result == []


def test_list_open_includes_andon_with_fenced_summary(local_client, tmp_path):
    """refs #7639 AC-2."""
    number = local_client.issue_create("andon fenced summary", "body")
    a = _make_andon(
        andon_id=f"wf:{number}:cp2:0",
        issue=number,
        summary=_FENCED_TEXT,
        options=["resume", "reject"],
        default="resume",
    )
    raise_andon(local_client, a, metrics_path=tmp_path / "metrics.jsonl")
    assert a.id in [x.id for x in list_open(local_client)]


def test_list_open_skips_comments_without_andon_block():
    client = _fake_client(
        issues=[{"number": 5}],
        comments=[{"body": "regular comment", "id": 1}],
    )
    with patch("issuesmith.andon._iter_open_andon_issues") as mock_iter:
        mock_iter.return_value = iter([{"number": 5}])
        client._stored_comments = [{"body": "regular comment", "id": 1}]
        result = list_open(client)
    assert result == []


# ---------------------------------------------------------------------------
# answer
# ---------------------------------------------------------------------------

def test_answer_rejects_unknown_answered_by(tmp_path):
    client = _fake_client()
    mfile = tmp_path / "metrics.jsonl"
    with pytest.raises(ValueError):
        answer(client, "wf:15:s:0", "handoff", metrics_path=mfile, answered_by="robot")
    assert not mfile.exists()
    assert client.issue_comments == []


def test_answer_raises_if_andon_not_found():
    client = _fake_client(comments=[{"body": "no andon here", "id": 1}])
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
    ):
        mock_iter.return_value = iter([{"number": 5}])
        client._stored_comments = [{"body": "no andon here", "id": 1}]
        with pytest.raises(KeyError):
            answer(client, "wf:5:s:0", "yes")


# ---------------------------------------------------------------------------
# AndonSink protocol
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CLI via __main__
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LocalForge round trip: list_open_records / note / answer (nexus #3678)
# ---------------------------------------------------------------------------

@pytest.fixture
def local_client(tmp_path, monkeypatch):
    import yaml
    from ghdag.forge import get_forge

    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    yield get_forge()
    reset_config_cache()


def _raise_local(client, tmp_path, *, andon_id_suffix: str = "cp2:0", number: int | None = None) -> Andon:
    if number is None:
        number = client.issue_create("andon target", "body")
    a = _make_andon(
        andon_id=f"wf:{number}:{andon_id_suffix}",
        issue=number,
        options=["resume", "reject"],
        default="resume",
    )
    raise_andon(client, a, metrics_path=tmp_path / "metrics.jsonl")
    return a


def test_note_does_not_touch_labels(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    with patch.object(local_client, "issue_update") as mock_update:
        note(local_client, a.id, "asked", "yes")
    mock_update.assert_not_called()


def test_answered_andon_excluded_when_sibling_is_open(local_client, tmp_path):
    from issuesmith.andon import list_open_records

    first = _raise_local(local_client, tmp_path, andon_id_suffix="cp2:0")
    second = _raise_local(local_client, tmp_path, andon_id_suffix="cp2:1", number=first.issue)
    with patch("issuesmith.andon._call_resume_hook"):
        answer(local_client, first.id, "resume", metrics_path=tmp_path / "metrics.jsonl")
    # answer() removed the shared label; put it back so the Issue is still listed.
    local_client.issue_update(first.issue, labels_add=[f"{_ns()}:andon-decision"])

    assert [x.id for x in list_open(local_client)] == [second.id]
    assert [r["id"] for r in list_open_records(local_client)] == [second.id]


def test_note_unknown_id_raises_key_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(KeyError):
        note(local_client, "wf:999:cp2:0", "asked", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_note_empty_key_raises_value_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(ValueError):
        note(local_client, a.id, "", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_andon_label_kinds_come_from_contract():
    from issuesmith.andon import _andon_label_kinds
    from issuesmith.contract import ANDON_KINDS

    assert _andon_label_kinds() == list(ANDON_KINDS)


def test_parse_note_round_trips_fenced_value():
    """refs #7639 AC-3."""
    from issuesmith.andon import _note_comment, _parse_note

    andon_id = "wf:1:cp2:0"
    body = _note_comment(andon_id, "review", _FENCED_TEXT)
    parsed = _parse_note(body)
    assert parsed == (andon_id, "review", _FENCED_TEXT)


# ---------------------------------------------------------------------------
# raise_andon drops queue tracking for stopped issues (#7464)
# ---------------------------------------------------------------------------

def _tmp_store(tmp_path):
    from issuesmith.queue_store import QueueStore

    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


@pytest.mark.parametrize("fail_on", ["init", "drop"])
def test_raise_andon_continues_when_queue_store_fails(tmp_path, caplog, fail_on):
    client = _fake_client()
    sink = RecordingSink()
    a = _make_andon(issue=10, kind="blocked")
    mfile = tmp_path / "metrics.jsonl"
    if fail_on == "init":
        store_patch = patch("issuesmith.andon.QueueStore", side_effect=OSError("boom"))
    else:
        broken = MagicMock()
        broken.drop_stopped_issue.side_effect = OSError("boom")
        store_patch = patch("issuesmith.andon.QueueStore", return_value=broken)
    with store_patch, caplog.at_level("WARNING", logger="issuesmith.andon"):
        raise_andon(client, a, sinks=[sink], metrics_path=mfile)
    warnings = [r for r in caplog.records if r.name == "issuesmith.andon"]
    assert len(warnings) == 1
    assert warnings[0].levelname == "WARNING"
    assert warnings[0].exc_info is not None
    assert len(client.issue_comments) == 1
    assert len(client.issue_updates) == 1
    assert f"{_ns()}:andon-blocked" in client.issue_updates[0][1].get("labels_add", [])
    assert sink.emitted == [a]
    assert json.loads(mfile.read_text().strip())["event"] == "andon_raised"


def test_raise_andon_drops_stopped_issue(tmp_path):
    from issuesmith.andon import raise_andon

    store = _tmp_store(tmp_path)
    store.add_in_flight(16, "claude")
    a = _make_andon(issue=16, kind="broken", step="p1")
    client = _fake_client()
    with (
        patch("issuesmith.andon.QueueStore", return_value=store),
        patch("issuesmith.andon._write_metrics"),
    ):
        raise_andon(client, a, metrics_path=tmp_path / "m.jsonl")
    snap = store.snapshot()
    assert snap.in_flight == []


# ---------------------------------------------------------------------------
# Cause classification and answer actions (Issue #8075)
# ---------------------------------------------------------------------------


def test_transient_rule_yields_agent_may_resume():
    """Refs #8075 AC-1."""
    from issuesmith.andon import Andon, answer_actions, classify_cause

    andon = Andon(
        id="wf:1:p1:0",
        kind="blocked",
        issue=1,
        step="p1",
        summary="interrupted",
        rule_id="tests.interrupted",
        options=[],
    )
    assert classify_cause(andon) == "transient"
    actions = answer_actions(andon, prior_reruns=0)
    assert actions[0]["action"] == "resume_from_p1"
    assert actions[0]["op"] == "resume"
    assert actions[0]["force"] is True
    assert actions[0]["agent_may_answer"] is True
    assert actions[-1]["action"] == "reject"
    assert actions[-1]["op"] == "record"
    assert actions[-1]["agent_may_answer"] is False


def test_repair_step_unknown_cause_blocks_agent_resume():
    """Refs #8075 AC-2."""
    from issuesmith.andon import Andon, answer_actions, classify_cause, fix_kinds_for

    andon = Andon(
        id="wf:1:repair:0",
        kind="decision",
        issue=1,
        step="repair",
        summary="repair gave up",
        facts={
            "failed_step": "p1",
            "trigger": "step",
            "rule_ids": ["repair.gave_up"],
        },
    )
    assert classify_cause(andon) == "unknown"
    actions = answer_actions(andon, prior_reruns=0)
    resume = next(a for a in actions if a["action"] == "resume_from_p1")
    assert resume["agent_may_answer"] is False
    assert fix_kinds_for(andon, actions) == ["diagnose"]


def test_issue_design_wins_over_transient_rule_ids():
    """Refs #8075 AC-3."""
    from issuesmith.andon import Andon, answer_actions, classify_cause, fix_kinds_for

    andon = Andon(
        id="wf:1:cp2:0",
        kind="decision",
        issue=1,
        step="cp2",
        summary="scope",
        facts={
            "failed_step": "cp2",
            "trigger": "non_repairable",
            "rule_ids": ["design_lint.yaml_contract", "tests.interrupted"],
        },
    )
    assert classify_cause(andon) == "issue_design"
    actions = answer_actions(andon, prior_reruns=0)
    assert fix_kinds_for(andon, actions) == ["edit_issue"]
    assert all(not a["agent_may_answer"] for a in actions)


def test_review_prefix_and_explicit_empty_rule_ids():
    from issuesmith.andon import Andon, classify_cause

    review = Andon(
        id="wf:1:cp2:0",
        kind="decision",
        issue=1,
        step="cp2",
        summary="review failed",
        rule_id="review.cp2.failed",
    )
    assert classify_cause(review) == "review_failed"

    explicit_empty = Andon(
        id="wf:1:p1:0",
        kind="blocked",
        issue=1,
        step="p1",
        summary="facts take precedence",
        rule_id="tests.interrupted",
        facts={"failed_step": "p1", "rule_ids": []},
    )
    assert classify_cause(explicit_empty) == "unknown"


def test_answer_actions_does_not_invent_reject_for_nonempty_options():
    from issuesmith.andon import Andon, answer_actions

    andon = Andon(
        id="wf:1:p1:0",
        kind="blocked",
        issue=1,
        step="p1",
        summary="",
        options=["retry_later"],
    )
    assert [a["action"] for a in answer_actions(andon)] == [
        "resume_from_p1",
        "retry_later",
    ]


@pytest.mark.parametrize(
    ("facts", "expected_cause", "expected_fix"),
    [
        ({"failure_class": "ENGINE_ENVIRONMENT_ERROR"}, "environment", ["clean_env"]),
        ({"failure_class": "TIMEOUT"}, "transient", None),
        ({"trigger": "gate_error", "rule_ids": []}, "line_defect", ["fix_line"]),
    ],
)
def test_failure_class_and_gate_trigger(facts, expected_cause, expected_fix):
    """Refs #8075 AC-4."""
    from issuesmith.andon import Andon, answer_actions, classify_cause, fix_kinds_for

    base = {"failed_step": "p1", "rule_ids": ["misc.unknown"]}
    andon = Andon(
        id="wf:1:p1:0",
        kind="broken",
        issue=1,
        step="p1",
        summary="s",
        facts={**base, **facts},
    )
    assert classify_cause(andon) == expected_cause
    actions = answer_actions(andon, prior_reruns=0)
    if expected_fix is not None:
        assert fix_kinds_for(andon, actions) == expected_fix


def test_prior_reruns_and_answer_markers(local_client, tmp_path):
    """Refs #8075 AC-5."""
    from issuesmith.andon import (
        Andon,
        answer,
        from_comment,
        list_open_records,
        raise_andon,
        to_comment,
    )

    number = local_client.issue_create("ac5", "body")
    first = Andon(
        id=f"wf:{number}:p1:0",
        kind="blocked",
        issue=number,
        step="p1",
        summary="interrupted",
        rule_id="tests.interrupted",
        facts={"failed_step": "p1", "trigger": "step", "rule_ids": ["tests.interrupted"]},
    )
    raise_andon(local_client, first, metrics_path=tmp_path / "m.jsonl")
    with patch("issuesmith.andon._call_resume_hook"):
        answer(
            local_client,
            first.id,
            "resume_from_p1",
            metrics_path=tmp_path / "m.jsonl",
            answered_by="agent",
        )
    bodies = [str(c.get("body") or "") for c in local_client.get_issue_comments(number)]
    assert any("<!-- andon-answer-action: resume_from_p1 -->" in b for b in bodies)
    assert any("<!-- andon-answered-by: agent -->" in b for b in bodies)

    second = Andon(
        id=f"wf:{number}:p1:1",
        kind="blocked",
        issue=number,
        step="p1",
        summary="interrupted again",
        rule_id="tests.interrupted",
        facts={"failed_step": "p1", "trigger": "step", "rule_ids": ["tests.interrupted"]},
    )
    raise_andon(local_client, second, metrics_path=tmp_path / "m.jsonl")
    bodies = [str(c.get("body") or "") for c in local_client.get_issue_comments(number)]
    comment_body = next(b for b in reversed(bodies) if "```andon" in b)
    parsed = from_comment(comment_body)
    assert parsed is not None
    assert parsed.facts == second.facts
    assert "cause:" not in comment_body or "cause" in comment_body

    records = list_open_records(local_client)
    second_record = next(r for r in records if r["id"] == second.id)
    assert second_record["prior_reruns"] == 1
    resume = next(a for a in second_record["actions"] if a["action"] == "resume_from_p1")
    assert resume["agent_may_answer"] is False
    assert second_record["fix_kinds"] == ["diagnose"]

    ignore = to_comment(
        Andon(
            id="ignore",
            kind="blocked",
            issue=1,
            step="p1",
            summary="",
            facts=second.facts,
            rule_id="tests.interrupted",
        ),
        prior_reruns=9,
    )
    legacy_roundtrip = from_comment(ignore + "\ncause: stale\nfix_kinds: [stale]")
    assert legacy_roundtrip is not None
    assert legacy_roundtrip.facts == second.facts

