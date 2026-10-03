"""Andon GitHub text comes from the language pack (nexus #4471)."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest
import yaml

from issuesmith.andon import Andon, answer, list_open, raise_andon
from issuesmith.language import EN, LanguagePack

_CUSTOM_MESSAGES = {
    "andon.answered": "lantern {andon_id} got reply {action}",
    "andon.widen_failed": "no metadata block; wanted {files}",
}


def _write_pack(path, messages: dict[str, str]) -> None:
    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(EN, f.name)
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, str):
            value = dict(value)
        data[f.name] = value
    data["messages"] = {**data["messages"], **messages}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """Return a factory for a LocalForge client, optionally with a custom pack."""
    from ghdag.forge import get_forge

    from issuesmith.config import reset_config_cache

    def _make(*, custom_pack: bool):
        payload: dict = {"repo": "example/repo"}
        if custom_pack:
            _write_pack(tmp_path / "pack.yaml", _CUSTOM_MESSAGES)
            payload["language_pack"] = "pack.yaml"
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        monkeypatch.setenv("GHDAG_FORGE", "local")
        monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
        reset_config_cache()
        return get_forge()

    yield _make
    reset_config_cache()


def _raise(client, tmp_path) -> Andon:
    number = client.issue_create("andon target", "body")
    a = Andon(
        id=f"wf:{number}:cp2:0",
        kind="decision",
        issue=number,
        step="cp2",
        summary="Should we proceed?",
        options=["resume", "reject"],
        default="resume",
    )
    raise_andon(client, a, metrics_path=tmp_path / "metrics.jsonl")
    return a


def _answer_comment(client, issue: int) -> str:
    bodies = [str(c.get("body") or "") for c in client.get_issue_comments(issue)]
    replies = [b for b in bodies if b.startswith("<!-- andon-answer -->")]
    assert len(replies) == 1
    return replies[0]


def test_answer_comment_is_english_with_default_pack(make_client, tmp_path):
    client = make_client(custom_pack=False)
    a = _raise(client, tmp_path)
    with patch("issuesmith.andon._call_resume_hook"):
        answer(client, a.id, "resume", metrics_path=tmp_path / "metrics.jsonl")
    reply = _answer_comment(client, a.issue)
    assert f"andon `{a.id}` answered: **resume**" in reply
    assert list_open(client) == []


def test_answer_comment_uses_custom_pack_and_stays_parseable(make_client, tmp_path):
    client = make_client(custom_pack=True)
    first = _raise(client, tmp_path)
    second = Andon(
        id=f"wf:{first.issue}:cp2:1",
        kind="decision",
        issue=first.issue,
        step="cp2",
        summary="Another question?",
        options=["resume"],
    )
    raise_andon(client, second, metrics_path=tmp_path / "metrics.jsonl")
    with patch("issuesmith.andon._call_resume_hook"):
        answer(client, first.id, "resume", metrics_path=tmp_path / "metrics.jsonl")
    reply = _answer_comment(client, first.issue)
    assert f"lantern {first.id} got reply resume" in reply
    assert "answered:" not in reply
    # The label was removed by answer(); restore it so the Issue is still scanned.
    from issuesmith.andon import _ns

    client.issue_update(first.issue, labels_add=[f"{_ns()}:andon-decision"])
    assert [x.id for x in list_open(client)] == [second.id]


def _widen_failed_comment(issue_body: str) -> str:
    from issuesmith.andon import _handle_widen_action

    client = MagicMock()
    client.issue_get.return_value = {"body": issue_body}
    with patch("issuesmith.andon.resume") as mock_resume:
        _handle_widen_action(client, 7, "p1", ["src/new.py"])
    mock_resume.assert_not_called()
    client.issue_comment.assert_called_once()
    issue, body = client.issue_comment.call_args[0]
    assert issue == 7
    return body


def test_widen_failed_comment_is_english_with_default_pack(make_client):
    make_client(custom_pack=False)
    body = _widen_failed_comment("no yaml here")
    assert body.startswith("<!-- andon-widen-failed -->\n")
    assert "Cannot widen allow_paths" in body
    assert "src/new.py" in body


def test_widen_failed_comment_uses_custom_pack(make_client):
    make_client(custom_pack=True)
    body = _widen_failed_comment("no yaml here")
    assert body.startswith("<!-- andon-widen-failed -->\n")
    assert "no metadata block; wanted ['src/new.py']" in body
    assert "Cannot widen" not in body
