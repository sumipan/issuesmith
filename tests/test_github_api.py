import sys
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

import issuesmith.github_api as github_api_module
from issuesmith.config import get_config

_CLI = [sys.executable, "-m", "ghdag.github_cli"]
_CONFIGURED_REPO = get_config().repo


def _mock_config_repo(repo: str):
    mock_cfg = MagicMock()
    mock_cfg.repo = repo
    return patch("issuesmith.github_api.get_config", return_value=mock_cfg)


def test_main_invokes_ghdag_cli_subprocess():
    """Refs: nexus#6571."""
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
        mock_run.return_value = CompletedProcess(args=[], returncode=0)
        with patch.object(sys, "argv", ["github_api", "issue", "view", "42"]):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    mock_run.assert_called_once_with(
        [*_CLI, "issue", "view", "42"],
        check=False,
    )
    assert exc_info.value.code == 0


def test_main_invokes_cli_without_args():
    """Refs: nexus#6571."""
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
        mock_run.return_value = CompletedProcess(args=[], returncode=1)
        with patch.object(sys, "argv", ["github_api"]):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    mock_run.assert_called_once_with([*_CLI], check=False)
    assert exc_info.value.code == 1


def test_main_passes_subprocess_exit_code():
    """Refs: nexus#6571."""
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
        mock_run.return_value = CompletedProcess(args=[], returncode=2)
        with patch.object(sys, "argv", ["github_api", "issue", "view", "999"]):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    assert exc_info.value.code == 2


def test_main_blocks_ready_label_before_subprocess(capsys):
    """Refs: nexus#6571."""
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
        with patch.object(
            sys,
            "argv",
            [
                "github_api",
                "issue",
                "edit",
                "1",
                "--add-label",
                "issuesmith:develop-ready",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    assert exc_info.value.code == 2
    mock_run.assert_not_called()
    err = capsys.readouterr().err
    assert "python3 -m issuesmith queue enqueue" in err


def test_main_blocks_foreign_issue_create_before_subprocess(capsys):
    """Refs: nexus#6571."""
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
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
                "other/repo",
            ],
        ):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    assert exc_info.value.code == 2
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "Issue creation is allowed only in" in captured.err
    assert "other/repo" in captured.err


# --- Issue #2819: pin issue create target to nexus ---


def test_issue_create_target_returns_none_for_non_create():
    assert github_api_module._issue_create_target(["pr", "list"], _CONFIGURED_REPO) is None
    assert github_api_module._issue_create_target(["issue", "view", "1"], _CONFIGURED_REPO) is None
    assert github_api_module._issue_create_target(
        ["issue", "view", "--repo", "sumipan/issuesmith", "1"],
        _CONFIGURED_REPO,
    ) is None
    assert github_api_module._issue_create_target(
        ["pr", "list", "--repo", "sumipan/issuesmith"],
        _CONFIGURED_REPO,
    ) is None


def test_issue_create_target_returns_none_when_repo_omitted():
    assert github_api_module._issue_create_target(
        ["issue", "create", "--title", "t"],
        _CONFIGURED_REPO,
    ) is None


def test_issue_create_target_returns_none_for_configured_repo():
    assert github_api_module._issue_create_target(
        ["issue", "create", "--title", "t", "--repo", _CONFIGURED_REPO],
        _CONFIGURED_REPO,
    ) is None
    assert github_api_module._issue_create_target(
        ["issue", "create", "--title", "t", f"--repo={_CONFIGURED_REPO}"],
        _CONFIGURED_REPO,
    ) is None


def test_issue_create_target_returns_foreign_repo():
    assert (
        github_api_module._issue_create_target(
            ["issue", "create", "--title", "t", "--repo", "owner-b/repo-b"],
            "owner-a/repo-a",
        )
        == "owner-b/repo-b"
    )
    assert (
        github_api_module._issue_create_target(
            ["issue", "create", "--title", "t", "--repo=owner-b/repo-b"],
            "owner-a/repo-a",
        )
        == "owner-b/repo-b"
    )


def test_main_blocks_issue_create_to_foreign_repo(capsys):
    with _mock_config_repo("owner-a/repo-a"):
        with patch("issuesmith.github_api.subprocess.run") as mock_run:
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
                    "owner-b/repo-b",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    github_api_module.main()
    assert exc_info.value.code == 2
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "Issue creation is allowed only in" in captured.err
    assert "owner-a/repo-a" in captured.err
    assert "owner-b/repo-b" in captured.err


def test_main_allows_issue_create_without_repo_injects_configured():
    with _mock_config_repo("owner-a/repo-a"):
        with patch("issuesmith.github_api.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(args=[], returncode=0)
            with patch.object(
                sys, "argv", ["github_api", "issue", "create", "--title", "t"]
            ):
                with pytest.raises(SystemExit) as exc_info:
                    github_api_module.main()
    mock_run.assert_called_once_with(
        [*_CLI, "issue", "create", "--title", "t", "--repo", "owner-a/repo-a"],
        check=False,
    )
    assert exc_info.value.code == 0


def test_main_allows_issue_create_to_configured_repo():
    with _mock_config_repo("owner-a/repo-a"):
        with patch("issuesmith.github_api.subprocess.run") as mock_run:
            mock_run.return_value = CompletedProcess(args=[], returncode=0)
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
                    "owner-a/repo-a",
                ],
            ):
                with pytest.raises(SystemExit) as exc_info:
                    github_api_module.main()
    mock_run.assert_called_once_with(
        [*_CLI, "issue", "create", "--title", "t", "--repo", "owner-a/repo-a"],
        check=False,
    )
    assert exc_info.value.code == 0


def test_main_allows_non_create_with_foreign_repo():
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
        mock_run.return_value = CompletedProcess(args=[], returncode=0)
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
    mock_run.assert_called_once()
    assert exc_info.value.code == 0


# --- Issue #3038: ready-label blocked → unified queue enqueue CLI hint ---


@pytest.mark.parametrize(
    "argv",
    [
        ["github_api", "issue", "edit", "1", "--add-label", "issuesmith:draft-ready"],
        ["github_api", "issue", "edit", "1", "--add-label=issuesmith:develop-ready"],
        ["github_api", "issue", "edit", "1", "--add-label", "issuesmith:merge-ready"],
    ],
)
def test_main_blocks_ready_label_with_unified_queue_enqueue_hint(argv, capsys):
    with patch("issuesmith.github_api.subprocess.run") as mock_run:
        with patch.object(sys, "argv", argv):
            with pytest.raises(SystemExit) as exc_info:
                github_api_module.main()
    assert exc_info.value.code == 2
    mock_run.assert_not_called()
    err = capsys.readouterr().err
    assert "python3 -m issuesmith queue enqueue" in err
    assert ("issuesmith" + ".queue enqueue") not in err
