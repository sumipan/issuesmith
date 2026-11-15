"""#3590: resume --from must pass a workflow name to ghdag dag recover."""

from __future__ import annotations

from unittest.mock import patch


def test_cmd_resume_passes_workflow_and_handler():
    from issuesmith.cli import _cmd_resume

    with patch("issuesmith.resume.resume", return_value=0) as mock_resume:
        rc = _cmd_resume(["3541", "--from", "p3", "--workflow", "issuesmith", "--handler", "impl"])
    assert rc == 0
    mock_resume.assert_called_once_with(
        3541, from_step="p3", phase=None, workflow="issuesmith", handler="impl"
    )
