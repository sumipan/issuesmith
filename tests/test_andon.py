"""Unit tests for issuesmith.andon — Andon dataclass, serialize/parse, raise/list/answer."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import (
    Andon,
    AndonSink,
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


def _fake_client(*, comments: list[dict] | None = None, issues: list[dict] | None = None) -> MagicMock:
    client = MagicMock()
    client.issue_comment.return_value = {"id": 999, "body": ""}
    client.get_issue_comments.return_value = comments or []
    client.list_issues.return_value = issues or []
    return client


# ---------------------------------------------------------------------------
# Andon dataclass — basic construction
# ---------------------------------------------------------------------------

def test_andon_fields():
    a = _make_andon()
    assert a.id == "myflow:42:cp2:0"
    assert a.kind == "decision"
    assert a.issue == 42
    assert a.step == "cp2"
    assert a.summary == "Should we proceed?"
    assert a.evidence == "test evidence"
    assert a.options == ["yes", "no"]
    assert a.default == "yes"
    assert a.mention == "@sumipan"


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


def test_round_trip():
    a = _make_andon()
    body = to_comment(a)
    parsed = from_comment(body)
    assert parsed is not None
    assert parsed.id == a.id
    assert parsed.kind == a.kind
    assert parsed.issue == a.issue
    assert parsed.step == a.step
    assert parsed.summary == a.summary
    assert parsed.evidence == a.evidence
    assert parsed.options == a.options
    assert parsed.default == a.default
    assert parsed.mention == a.mention


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


# ---------------------------------------------------------------------------
# raise_andon
# ---------------------------------------------------------------------------

def _ns() -> str:
    from issuesmith.config import get_config
    return get_config().label_namespace


def test_raise_andon_posts_comment():
    client = _fake_client()
    a = _make_andon(issue=10)
    with patch("issuesmith.andon._write_metrics"):
        raise_andon(client, a)
    client.issue_comment.assert_called_once()
    args = client.issue_comment.call_args
    assert args[0][0] == 10


def test_raise_andon_comment_body_contains_andon_block():
    client = _fake_client()
    a = _make_andon(issue=10)
    with patch("issuesmith.andon._write_metrics"):
        raise_andon(client, a)
    body = client.issue_comment.call_args[0][1]
    assert "```andon" in body


def test_raise_andon_adds_label():
    client = _fake_client()
    a = _make_andon(issue=10, kind="decision")
    with patch("issuesmith.andon._write_metrics"):
        raise_andon(client, a)
    client.issue_update.assert_called_once()
    kwargs = client.issue_update.call_args[1]
    ns = _ns()
    assert f"{ns}:andon-decision" in kwargs.get("labels_add", [])


def test_raise_andon_label_uses_config_namespace(monkeypatch):
    import issuesmith.config as cfg_module
    # Build a minimal config with a custom namespace
    real_cfg = cfg_module.get_config()
    # monkeypatch get_config to return custom label_namespace
    import dataclasses
    custom = dataclasses.replace(real_cfg, label_namespace="myns")
    monkeypatch.setattr(cfg_module, "_cached", custom)

    client = _fake_client()
    a = _make_andon(issue=5, kind="blocked")
    with patch("issuesmith.andon._write_metrics"):
        raise_andon(client, a)
    kwargs = client.issue_update.call_args[1]
    assert "myns:andon-blocked" in kwargs.get("labels_add", [])

    cfg_module.reset_config_cache()


def test_raise_andon_calls_sinks():
    client = _fake_client()
    sink = MagicMock()
    a = _make_andon(issue=10)
    with patch("issuesmith.andon._write_metrics"):
        raise_andon(client, a, sinks=[sink])
    sink.emit.assert_called_once_with(a)


def test_raise_andon_writes_metrics(tmp_path):
    client = _fake_client()
    a = _make_andon(issue=10)
    mfile = tmp_path / "metrics.jsonl"
    raise_andon(client, a, metrics_path=mfile)
    assert mfile.exists()
    line = json.loads(mfile.read_text().strip())
    assert line["event"] == "andon_raised"
    assert line["andon_id"] == a.id


# ---------------------------------------------------------------------------
# list_open
# ---------------------------------------------------------------------------

def test_list_open_returns_empty_when_no_issues():
    client = _fake_client(issues=[])
    with patch("issuesmith.andon._andon_label_kinds", return_value=["decision", "blocked", "broken"]):
        result = list_open(client)
    assert result == []


def test_list_open_finds_andon_in_comment():
    a = _make_andon(issue=7, andon_id="wf:7:step:0")
    comment_body = to_comment(a)
    client = _fake_client(
        issues=[{"number": 7, "labels": []}],
        comments=[{"body": comment_body, "id": 1}],
    )
    with patch("issuesmith.andon._iter_open_andon_issues") as mock_iter:
        mock_iter.return_value = iter([{"number": 7}])
        client.get_issue_comments.return_value = [{"body": comment_body, "id": 1}]
        result = list_open(client)
    assert len(result) == 1
    assert result[0].id == "wf:7:step:0"


def test_list_open_skips_comments_without_andon_block():
    client = _fake_client(
        issues=[{"number": 5}],
        comments=[{"body": "regular comment", "id": 1}],
    )
    with patch("issuesmith.andon._iter_open_andon_issues") as mock_iter:
        mock_iter.return_value = iter([{"number": 5}])
        client.get_issue_comments.return_value = [{"body": "regular comment", "id": 1}]
        result = list_open(client)
    assert result == []


# ---------------------------------------------------------------------------
# answer
# ---------------------------------------------------------------------------

def test_answer_posts_comment():
    a = _make_andon(issue=15, andon_id="wf:15:s:0", kind="decision")
    comment_body = to_comment(a)
    client = _fake_client(
        issues=[{"number": 15}],
        comments=[{"body": comment_body, "id": 10}],
    )
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon._call_resume_hook"),
    ):
        mock_iter.return_value = iter([{"number": 15}])
        client.get_issue_comments.return_value = [{"body": comment_body, "id": 10}]
        answer(client, "wf:15:s:0", "yes")
    client.issue_comment.assert_called_once()
    args = client.issue_comment.call_args[0]
    assert args[0] == 15
    assert "yes" in args[1]


def test_answer_removes_label():
    a = _make_andon(issue=15, andon_id="wf:15:s:0", kind="decision")
    comment_body = to_comment(a)
    client = _fake_client(
        comments=[{"body": comment_body, "id": 10}],
    )
    ns = _ns()
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon._call_resume_hook"),
    ):
        mock_iter.return_value = iter([{"number": 15}])
        client.get_issue_comments.return_value = [{"body": comment_body, "id": 10}]
        answer(client, "wf:15:s:0", "yes")
    client.issue_update.assert_called_once()
    kwargs = client.issue_update.call_args[1]
    assert f"{ns}:andon-decision" in kwargs.get("labels_remove", [])


def test_answer_calls_resume_hook():
    a = _make_andon(issue=15, andon_id="wf:15:s:0", kind="decision")
    comment_body = to_comment(a)
    client = _fake_client(
        comments=[{"body": comment_body, "id": 10}],
    )
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon._call_resume_hook") as mock_hook,
    ):
        mock_iter.return_value = iter([{"number": 15}])
        client.get_issue_comments.return_value = [{"body": comment_body, "id": 10}]
        answer(client, "wf:15:s:0", "yes")
    mock_hook.assert_called_once_with(client, "wf:15:s:0", "yes")


def test_answer_raises_if_andon_not_found():
    client = _fake_client(comments=[{"body": "no andon here", "id": 1}])
    with (
        patch("issuesmith.andon._iter_open_andon_issues") as mock_iter,
    ):
        mock_iter.return_value = iter([{"number": 5}])
        client.get_issue_comments.return_value = [{"body": "no andon here", "id": 1}]
        with pytest.raises(KeyError):
            answer(client, "wf:5:s:0", "yes")


# ---------------------------------------------------------------------------
# AndonSink protocol
# ---------------------------------------------------------------------------

def test_andon_sink_protocol():
    class MySink:
        def emit(self, andon: Andon) -> None:
            pass

    sink: AndonSink = MySink()  # type: ignore[assignment]
    assert hasattr(sink, "emit")


# ---------------------------------------------------------------------------
# CLI via __main__
# ---------------------------------------------------------------------------

def test_cli_andon_no_subcommand(capsys):
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[1]
    src_path = str(repo_root / "src")
    env = {**os.environ, "PYTHONPATH": src_path}
    result = subprocess.run(
        [sys.executable, "-m", "issuesmith", "andon"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert "list" in result.stderr or "usage" in result.stderr.lower()


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


def test_list_open_records_returns_raised_andon(local_client, tmp_path):
    from issuesmith.andon import list_open_records

    a = _raise_local(local_client, tmp_path)
    records = list_open_records(local_client)
    assert len(records) == 1
    rec = records[0]
    assert rec["id"] == a.id
    assert rec["options"] == ["resume", "reject"]
    assert rec["default"] == "resume"
    assert isinstance(rec["raised_at"], str) and rec["raised_at"]
    assert rec["notes"] == {}
    json.dumps(rec)


def test_note_is_folded_into_records_last_write_wins(local_client, tmp_path):
    from issuesmith.andon import list_open_records, note

    a = _raise_local(local_client, tmp_path)
    note(local_client, a.id, "asked", "2026-09-26T00:00:00Z")
    assert list_open_records(local_client)[0]["notes"] == {"asked": "2026-09-26T00:00:00Z"}

    note(local_client, a.id, "asked", "2026-09-27T00:00:00Z")
    note(local_client, a.id, "channel", "C123")
    assert list_open_records(local_client)[0]["notes"] == {
        "asked": "2026-09-27T00:00:00Z",
        "channel": "C123",
    }


def test_note_does_not_touch_labels(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    with patch.object(local_client, "issue_update") as mock_update:
        note(local_client, a.id, "asked", "yes")
    mock_update.assert_not_called()


def test_answer_removes_andon_from_list_and_records(local_client, tmp_path):
    from issuesmith.andon import list_open_records, note

    a = _raise_local(local_client, tmp_path)
    note(local_client, a.id, "asked", "yes")
    with patch("issuesmith.andon._call_resume_hook"):
        answer(local_client, a.id, "resume", metrics_path=tmp_path / "metrics.jsonl")
    assert [x.id for x in list_open(local_client)] == []
    assert list_open_records(local_client) == []


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


def test_list_open_excludes_answered_with_mock_client():
    a = _make_andon(issue=9, andon_id="wf:9:s:0")
    b = _make_andon(issue=9, andon_id="wf:9:s:1")
    comments = [
        {"body": to_comment(a), "created_at": "t1"},
        {"body": to_comment(b), "created_at": "t2"},
        {"body": "<!-- andon-answer -->\nandon `wf:9:s:0` answered: **yes**\n", "created_at": "t3"},
    ]
    client = _fake_client(comments=comments)
    with patch("issuesmith.andon._iter_open_andon_issues", return_value=iter([{"number": 9}])):
        result = list_open(client)
    assert [x.id for x in result] == ["wf:9:s:1"]
    client.get_issue_comments.assert_called_once_with(9)


def test_note_unknown_id_raises_key_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(KeyError):
        note(local_client, "wf:999:cp2:0", "asked", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_note_answered_id_raises_key_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    with patch("issuesmith.andon._call_resume_hook"):
        answer(local_client, a.id, "resume", metrics_path=tmp_path / "metrics.jsonl")
    # keep the Issue listed so the answered andon is still scanned
    local_client.issue_update(a.issue, labels_add=[f"{_ns()}:andon-decision"])
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(KeyError):
        note(local_client, a.id, "asked", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_note_empty_key_raises_value_error(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    before = len(local_client.get_issue_comments(a.issue))
    with pytest.raises(ValueError):
        note(local_client, a.id, "", "yes")
    assert len(local_client.get_issue_comments(a.issue)) == before


def test_note_comment_is_not_parsed_as_andon(local_client, tmp_path):
    from issuesmith.andon import note

    a = _raise_local(local_client, tmp_path)
    note(local_client, a.id, "asked", "2026-09-26T00:00:00Z")
    last = local_client.get_issue_comments(a.issue)[-1]["body"]
    assert last.startswith("<!-- andon-note -->")
    assert "```andon-note" in last
    assert from_comment(last) is None
