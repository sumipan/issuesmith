"""Cross-repo closing PR detection for merge-phase ``closing_pr_exists`` (#5780)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import pytest

from issuesmith.config import get_config, reset_config_cache
from issuesmith.preconditions import PreconditionContext, _closing_pr_exists


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


def test_api_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ghdag.forge.get_forge",
        lambda repo=None: _FailForge(),
    )
    ok, why = _closing_pr_exists(_ctx(_cross_repo_body()), _nexus_config())
    assert ok is False
    assert why == "no open or merged PR with Closes #N"


