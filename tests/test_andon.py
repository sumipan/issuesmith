"""Unit tests for issuesmith.andon — Andon dataclass, serialize/parse, raise/list/answer."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from issuesmith.andon import (
    Andon,
    AndonSink,
    answer,
    list_open,
    raise_andon,
    to_comment,
    from_comment,
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
    from issuesmith.config import IssuesmithConfig
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
    import subprocess, sys, os
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
