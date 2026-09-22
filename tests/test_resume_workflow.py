"""#3590: resume --from must pass a workflow name to ghdag dag recover."""

from __future__ import annotations

from unittest.mock import patch

from issuesmith.recovery import _run_ghdag_recover


def test_run_ghdag_recover_includes_workflow_flag():
    with patch("issuesmith.recovery.subprocess.run") as run:
        run.return_value.returncode = 0
        rc = _run_ghdag_recover(3541, "impl", "p3", workflow="issuesmith")
    assert rc == 0
    cmd = run.call_args[0][0]
    assert "--workflow" in cmd
    assert cmd[cmd.index("--workflow") + 1] == "issuesmith"
    assert cmd[cmd.index("--from") + 1] == "p3"


def test_resume_from_step_defaults_workflow_to_config_stem():
    from issuesmith import resume as mod

    captured: dict = {}

    def fake_recover(issue, handler, from_step, workflow=None):
        captured.update(issue=issue, handler=handler, from_step=from_step, workflow=workflow)
        return 0

    with (
        patch.object(mod, "_run_ghdag_recover", fake_recover),
        patch.object(mod, "_generation_keys_available", return_value=True),
        patch.object(mod, "_default_workflow_name", return_value="issuesmith"),
    ):
        rc = mod._resume_from_step(3541, "p3")
    assert rc == 0
    assert captured["workflow"] == "issuesmith"
    assert captured["handler"] == "impl"


def test_cmd_resume_passes_workflow_and_handler():
    from issuesmith.cli import _cmd_resume

    with patch("issuesmith.resume.resume", return_value=0) as mock_resume:
        rc = _cmd_resume(["3541", "--from", "p3", "--workflow", "issuesmith", "--handler", "impl"])
    assert rc == 0
    mock_resume.assert_called_once_with(
        3541, from_step="p3", phase=None, workflow="issuesmith", handler="impl"
    )
