"""Tests for engine.run_guarded_output (#4986)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from issuesmith.engine import run_guarded_output


def test_run_guarded_output_success_rc(capsys) -> None:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "ok\nPIPELINE_STATUS: CP2_PASS\n"
    proc.stderr = ""

    with (
        patch("issuesmith.engine._execute", return_value=proc),
        patch("issuesmith.engine._render_template", return_value="order"),
    ):
        rc, stdout = run_guarded_output(
            "review",
            "/tmp/t.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )

    assert rc == 0
    assert "CP2_PASS" in stdout
    assert capsys.readouterr().out == ""


def test_run_guarded_output_failure_rc(capsys) -> None:
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "bad\nPIPELINE_STATUS: CP2_FAIL\n"
    proc.stderr = ""

    with (
        patch("issuesmith.engine._execute", return_value=proc),
        patch("issuesmith.engine._render_template", return_value="order"),
    ):
        rc, stdout = run_guarded_output(
            "review",
            "/tmp/t.md",
            [],
            ["CP2_PASS"],
            "CP2_FAIL",
        )

    assert rc == 1
    assert "CP2_FAIL" in stdout
    assert capsys.readouterr().out == ""
