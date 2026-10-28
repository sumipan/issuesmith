"""Closing-PR lookups read the raw PR list once instead of one request per PR.

The normalized ``pr_list`` strips ``body`` and merge metadata, so the old
lookup called ``pulls/{n}`` and ``pr_get`` (``pulls/{n}`` + ``files``) for up
to 100 PRs per Issue — ~350 calls per ``queue.plan`` on 2026-10-28.
"""

from __future__ import annotations

from typing import Any

from issuesmith.queue import _find_merged_prs_closing_issue, _find_open_prs_closing_issue


class _RawForge:
    def __init__(self, pulls: dict[str, list[dict[str, Any]]]) -> None:
        self._pulls = pulls
        self.calls: list[str] = []

    def api_request(self, path: str, **kwargs: Any) -> Any:
        self.calls.append(path)
        state = path.split("state=")[1].split("&")[0]
        return self._pulls[state]

    def pr_list(self, **kwargs: Any) -> Any:
        raise AssertionError("pr_list must not be used when the raw list is available")

    def pr_get(self, number: int, **kwargs: Any) -> Any:
        raise AssertionError("pr_get must not be used when the raw list is available")


def test_merged_lookup_uses_single_raw_list() -> None:
    client = _RawForge(
        {
            "closed": [
                {"number": 1, "title": "x", "body": "Refs #42", "merged_at": "2026-10-28T00:00:00Z"},
                {"number": 2, "title": "Closes #42", "body": None, "merged_at": None},
                {"number": 3, "title": "y", "body": "Refs #420", "merged_at": "2026-10-28T00:00:00Z"},
            ]
        }
    )

    merged = _find_merged_prs_closing_issue(client, 42)  # type: ignore[arg-type]

    assert [pr["number"] for pr in merged] == [1]
    assert client.calls == ["pulls?state=closed&per_page=100"]


def test_open_lookup_uses_single_raw_list() -> None:
    client = _RawForge(
        {
            "open": [
                {"number": 5, "title": "feat", "body": "Closes #7\n"},
                {"number": 6, "title": "other", "body": ""},
            ]
        }
    )

    matched = _find_open_prs_closing_issue(client, 7)  # type: ignore[arg-type]

    assert [pr["number"] for pr in matched] == [5]
    assert client.calls == ["pulls?state=open&per_page=100"]


class _NormalizedOnlyForge:
    """No ``api_request``: falls back to pr_list + pr_get."""

    def pr_list(self, **kwargs: Any) -> Any:
        return [{"number": 9, "title": "t", "state": "OPEN"}]

    def pr_get(self, number: int, **kwargs: Any) -> Any:
        return {"number": number, "title": "t", "body": "Refs #3"}


def test_open_lookup_falls_back_without_api_request() -> None:
    matched = _find_open_prs_closing_issue(_NormalizedOnlyForge(), 3)  # type: ignore[arg-type]
    assert [pr["number"] for pr in matched] == [9]
