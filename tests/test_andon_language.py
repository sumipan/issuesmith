"""Andon GitHub text comes from the language pack (nexus #4471)."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest
import yaml

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
