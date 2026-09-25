"""Tests for issuesmith.forge_api (#3611)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from issuesmith.forge_api import RawApiForge, api_request


class _Fake:
    def __init__(self, result=None):
        self.calls: list[tuple[str, dict]] = []
        self.result = result

    def api_request(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return self.result


def test_delegates_and_returns_value_unchanged():
    payload = [{"number": 1}]
    fake = _Fake(result=payload)
    assert api_request(fake, "issues?state=open", paginate=True) is payload
    assert fake.calls == [("issues?state=open", {"paginate": True})]


def test_get_method_is_not_forwarded():
    client = MagicMock()
    api_request(client, "pulls/1")
    client.api_request.assert_called_once_with("pulls/1")
    api_request(client, "pulls/2", method="GET")
    client.api_request.assert_called_with("pulls/2")


def test_non_get_method_is_forwarded():
    client = MagicMock()
    api_request(client, "issues/1/labels", method="POST", data={"x": 1})
    client.api_request.assert_called_once_with(
        "issues/1/labels", method="POST", data={"x": 1}
    )


def test_client_without_api_request_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="object does not support api_request"):
        api_request(object(), "pulls/1")


def test_runtime_protocol_check():
    assert isinstance(_Fake(), RawApiForge)
    assert not isinstance(object(), RawApiForge)


def test_list_open_issues_degrades_to_empty_list():
    from issuesmith.queue import _list_open_issues

    assert _list_open_issues(object()) == []


def test_list_milestone_children_degrades_to_empty_list():
    from issuesmith.milestone import _list_milestone_children

    assert _list_milestone_children(object(), 1) == []
