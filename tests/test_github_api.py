import sys
from unittest.mock import patch

import pytest
from ghdag.github_client import DEFAULT_REPO

import issuesmith.github_api as github_api_module


def test_main_delegates_to_cli_main():
    with patch("issuesmith.github_api.cli_main", return_value=0) as mock_cli:
        with patch.object(sys, "argv", ["github_api", "issue", "view", "1"]):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    mock_cli.assert_called_once_with(["issue", "view", "1"])
    assert exc_info.value.code == 0


def test_exit_code_passthrough():
    with patch("issuesmith.github_api.cli_main", return_value=2):
        with patch.object(sys, "argv", ["github_api", "issue", "view", "999"]):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    assert exc_info.value.code == 2


def test_no_args_shows_usage(capsys):
    with patch.object(sys, "argv", ["github_api"]):
        with pytest.raises(SystemExit) as exc_info:
            github_api_module.main()
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "usage" in captured.err.lower()


# --- Issue #2819: issue create の作成先を nexus に固定 ---


def test_issue_create_target_returns_none_for_non_create():
    assert github_api_module._issue_create_target(["pr", "list"]) is None
    assert github_api_module._issue_create_target(["issue", "view", "1"]) is None
    assert github_api_module._issue_create_target(
        ["issue", "view", "--repo", "sumipan/issuesmith", "1"]
    ) is None
    assert github_api_module._issue_create_target(
        ["pr", "list", "--repo", "sumipan/issuesmith"]
    ) is None


def test_issue_create_target_returns_none_when_repo_omitted():
    assert github_api_module._issue_create_target(
        ["issue", "create", "--title", "t"]
    ) is None


def test_issue_create_target_returns_none_for_default_repo():
    assert github_api_module._issue_create_target(
        ["issue", "create", "--title", "t", "--repo", DEFAULT_REPO]
    ) is None
    assert github_api_module._issue_create_target(
        ["issue", "create", "--title", "t", f"--repo={DEFAULT_REPO}"]
    ) is None


def test_issue_create_target_returns_non_nexus_repo():
    assert (
        github_api_module._issue_create_target(
            ["issue", "create", "--title", "t", "--repo", "sumipan/issuesmith"]
        )
        == "sumipan/issuesmith"
    )
    assert (
        github_api_module._issue_create_target(
            ["issue", "create", "--title", "t", "--repo=sumipan/ghdag"]
        )
        == "sumipan/ghdag"
    )


def test_main_blocks_issue_create_to_non_nexus(capsys):
    with patch("issuesmith.github_api.cli_main", return_value=0) as mock_cli:
        with patch.object(
            sys,
            "argv",
            [
                "github_api",
                "issue",
                "create",
                "--title",
                "t",
                "--repo",
                "sumipan/issuesmith",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    assert exc_info.value.code == 2
    mock_cli.assert_not_called()
    captured = capsys.readouterr()
    assert "Issue creation is allowed only in" in captured.err
    assert "sumipan/issuesmith" in captured.err


def test_main_allows_issue_create_without_repo():
    with patch("issuesmith.github_api.cli_main", return_value=0) as mock_cli:
        with patch.object(
            sys, "argv", ["github_api", "issue", "create", "--title", "t"]
        ):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    mock_cli.assert_called_once_with(["issue", "create", "--title", "t"])
    assert exc_info.value.code == 0


def test_main_allows_issue_create_to_nexus():
    with patch("issuesmith.github_api.cli_main", return_value=0) as mock_cli:
        with patch.object(
            sys,
            "argv",
            [
                "github_api",
                "issue",
                "create",
                "--title",
                "t",
                "--repo",
                DEFAULT_REPO,
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    mock_cli.assert_called_once()
    assert exc_info.value.code == 0


def test_main_allows_non_create_with_foreign_repo():
    with patch("issuesmith.github_api.cli_main", return_value=0) as mock_cli:
        with patch.object(
            sys,
            "argv",
            [
                "github_api",
                "issue",
                "view",
                "--repo",
                "sumipan/issuesmith",
                "1",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    mock_cli.assert_called_once()
    assert exc_info.value.code == 0
