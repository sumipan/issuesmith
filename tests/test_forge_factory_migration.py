"""Behaviour tests for forge client construction via get_forge() (#3102 / #3066 sub4)."""

from __future__ import annotations


def test_check_dependencies_uses_get_forge_when_client_omitted(monkeypatch):
    from issuesmith import dep_extractor as de

    calls: list[dict] = []

    class FakeForge:
        def issue_get(self, number, fields=None):
            return {
                "state": "CLOSED",
                "title": "done",
                "labels": [{"name": "issuesmith:merge-done"}],
            }

    def fake_get_forge(**kwargs):
        calls.append(kwargs)
        return FakeForge()

    monkeypatch.setattr(de, "get_forge", fake_get_forge)
    result = de.check_dependencies([1])
    assert result.decision == "PASS"
    assert calls == [{}]


def test_rescue_pr_uses_pr_get_merged_flag():
    from unittest.mock import MagicMock

    from issuesmith.dep_extractor import check_dependencies

    client = MagicMock()
    client.issue_get.return_value = {
        "state": "CLOSED",
        "title": "legacy closed",
        "labels": [{"name": "issuesmith:develop-done"}],
    }
    client.issue_timeline.return_value = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {"number": 42, "pull_request": {"url": "..."}},
            },
        }
    ]
    client.pr_get.return_value = {"merged": True, "state": "CLOSED"}

    pr_get_calls: list[int] = []
    api_request_calls: list[tuple] = []

    original_pr_get = client.pr_get

    def tracking_pr_get(number):
        pr_get_calls.append(number)
        return original_pr_get(number)

    client.pr_get = tracking_pr_get
    client.api_request.side_effect = lambda *a, **k: api_request_calls.append((a, k))

    result = check_dependencies([600], client=client)
    assert result.decision == "PASS"
    assert result.dep_statuses[0].rescue_pr == 42
    assert pr_get_calls == [42]
    assert api_request_calls == []
