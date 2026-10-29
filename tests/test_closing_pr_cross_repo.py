"""Cross-repo closing PR detection for merge-phase ``closing_pr_exists`` (#5780)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest

from issuesmith.config import get_config, reset_config_cache
from issuesmith.preconditions import PreconditionContext, _closing_pr_exists
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


class _FailForge:
    def api_request(self, path: str, **kwargs: Any) -> Any:
        raise RuntimeError("api down")

    def pr_list(self, **kwargs: Any) -> Any:
        raise RuntimeError("list down")

    def pr_get(self, number: int, **kwargs: Any) -> Any:
        raise RuntimeError("get down")


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _ctx(body: str, *, issue_number: int = 5780, client: Any | None = None) -> PreconditionContext:
    return PreconditionContext(
        issue={"state": "OPEN", "labels": [], "body": body},
        labels=set(),
        client=client or MagicMock(),
        issue_number=issue_number,
    )


def _cross_repo_body(target: str = "sumipan/issuesmith") -> str:
    return f"```yaml\ntarget_repo: {target}\nbase_branch: main\n```\n"


def _nexus_config():
    return replace(get_config(), repo="sumipan/nexus")


def test_open_head_match_cross_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    forge = _RawForge(
        {
            "open": [
                {
                    "number": 272,
                    "title": "impl",
                    "body": "",
                    "head": {"ref": "feat/issue-5780-fix-closing-pr"},
                }
            ],
            "closed": [],
        }
    )
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: forge if repo == "sumipan/issuesmith" else MagicMock(),
    )
    ok, why = _closing_pr_exists(
        _ctx(_cross_repo_body(), issue_number=5780),
        _nexus_config(),
    )
    assert (ok, why) == (True, "ok")
    assert forge.calls == ["pulls?state=open&per_page=100"]


def test_open_qualified_ref_cross_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    forge = _RawForge(
        {
            "open": [
                {"number": 272, "title": "impl", "body": "Refs sumipan/nexus#5780\n"},
            ],
            "closed": [],
        }
    )
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: forge if repo == "sumipan/issuesmith" else MagicMock(),
    )
    ok, why = _closing_pr_exists(_ctx(_cross_repo_body()), _nexus_config())
    assert (ok, why) == (True, "ok")


def test_wrong_repo_ref_not_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    forge = _RawForge(
        {
            "open": [{"number": 1, "title": "x", "body": "Refs other/repo#5780"}],
            "closed": [],
        }
    )
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: forge if repo == "sumipan/issuesmith" else MagicMock(),
    )
    ok, why = _closing_pr_exists(_ctx(_cross_repo_body()), _nexus_config())
    assert ok is False
    assert why == "no open or merged PR with Closes #N"


def test_merged_already_merged_cross_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _nexus_config()
    merge_done = ""
    for ph in cfg.phases:
        if "closing_pr_exists" in ph.advance_when:
            _, _, merge_done = cfg.phase_labels(ph.name)
            break
    assert merge_done
    forge = _RawForge(
        {
            "open": [],
            "closed": [
                {
                    "number": 9,
                    "title": "done",
                    "body": "Refs sumipan/nexus#5780",
                    "merged_at": "2026-10-28T00:00:00Z",
                }
            ],
        }
    )
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: forge if repo == "sumipan/issuesmith" else MagicMock(),
    )
    ok, why = _closing_pr_exists(_ctx(_cross_repo_body()), cfg)
    assert (ok, why) == (True, "already_merged")


def test_no_target_repo_uses_ctx_client(monkeypatch: pytest.MonkeyPatch) -> None:
    native = _RawForge(
        {
            "open": [{"number": 5, "title": "t", "body": "Closes #5780"}],
            "closed": [],
        }
    )
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: pytest.fail("get_forge must not run without cross-repo target"),
    )
    ok, why = _closing_pr_exists(
        _ctx("```yaml\nbase_branch: main\n```\n", client=native),  # type: ignore[arg-type]
        get_config(),
    )
    assert (ok, why) == (True, "ok")
    assert native.calls == ["pulls?state=open&per_page=100"]


def test_api_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: _FailForge(),
    )
    ok, why = _closing_pr_exists(_ctx(_cross_repo_body()), _nexus_config())
    assert ok is False
    assert why == "no open or merged PR with Closes #N"


def test_issue_number_boundary_in_lookup() -> None:
    client = _RawForge(
        {
            "open": [
                {"number": 1, "title": "bad head", "headRefName": "feat/issue-57800-x"},
                {"number": 2, "title": "bad ref", "body": "Refs #57800"},
                {"number": 3, "title": "good", "body": "Refs #5780"},
            ]
        }
    )
    matched = _find_open_prs_closing_issue(
        client,  # type: ignore[arg-type]
        5780,
        issue_repo="sumipan/nexus",
    )
    assert [pr["number"] for pr in matched] == [3]


def test_head_boundary_issue_12_not_123() -> None:
    client = _RawForge(
        {
            "open": [
                {"number": 1, "headRefName": "feat/issue-123-abc", "title": "", "body": ""},
                {"number": 2, "headRefName": "feat/issue-12-more", "title": "", "body": ""},
            ]
        }
    )
    matched = _find_open_prs_closing_issue(
        client,  # type: ignore[arg-type]
        12,
        issue_repo="sumipan/nexus",
    )
    assert [pr["number"] for pr in matched] == [2]


def test_merged_lookup_single_raw_call() -> None:
    client = _RawForge(
        {
            "closed": [
                {
                    "number": 1,
                    "body": "Refs sumipan/nexus#5780",
                    "merged_at": "2026-10-28T00:00:00Z",
                }
            ]
        }
    )
    merged = _find_merged_prs_closing_issue(
        client,  # type: ignore[arg-type]
        5780,
        issue_repo="sumipan/nexus",
    )
    assert [pr["number"] for pr in merged] == [1]
    assert client.calls == ["pulls?state=closed&per_page=100"]
