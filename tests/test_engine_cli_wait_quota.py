"""engine CLI: RetrySignal -> WAIT_QUOTA + exit 0 (sumipan/nexus#5802)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from issuesmith.engine import RetryReason, RetrySignal, main


def _quota_paused_sig(*, role: str = "design") -> RetrySignal:
    after = datetime.now(timezone.utc) + timedelta(hours=1)
    return RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=after, role=role)


@pytest.fixture
def dispatch_forge(monkeypatch):
    from issuesmith.ops import dispatch

    forge = MagicMock()
    monkeypatch.setattr(dispatch, "get_forge", MagicMock(return_value=forge))
    return forge


@pytest.fixture
def stub_execute(monkeypatch):
    def install(exc: BaseException) -> None:
        monkeypatch.setattr(
            "issuesmith.engine._execute",
            MagicMock(side_effect=exc),
        )

    return install


@pytest.fixture
def template_path(tmp_path):
    path = tmp_path / "order.md"
    path.write_text("static prompt", encoding="utf-8")
    return path


@pytest.fixture
def prompt_path(tmp_path):
    path = tmp_path / "prompt.txt"
    path.write_text("hello", encoding="utf-8")
    return path


def _assert_wait_quota_stdout(capsys) -> None:
    captured = capsys.readouterr()
    assert "REJECTED:" not in captured.err
    lines = [ln for ln in captured.out.splitlines() if ln.strip()]
    wait_status = [ln for ln in lines if ln == "PIPELINE_STATUS: WAIT_QUOTA"]
    companion = [ln for ln in lines if ln.startswith("WAIT_QUOTA: after=")]
    assert len(wait_status) == 1
    assert len(companion) == 1


class TestEngineMainRetrySignalActions:
    @pytest.mark.parametrize(
        "argv_factory",
        [
            lambda prompt, template: ["exec", "design", str(prompt)],
            lambda prompt, template: ["run", "design", str(template)],
            lambda prompt, template: [
                "run-guarded",
                "design",
                str(template),
                "--success",
                "IMPL_DONE",
                "--failure-status",
                "IMPL_FAILED",
            ],
            lambda prompt, template: [
                "run-verified",
                "design",
                str(template),
                "--success",
                "IMPL_DONE",
                "--failure-status",
                "IMPL_FAILED",
                "--verify",
                "true",
                "--recover",
                str(template),
            ],
        ],
        ids=["exec", "run", "run-guarded", "run-verified"],
    )
    def test_cli_action_returns_wait_quota(
        self,
        argv_factory,
        prompt_path,
        template_path,
        dispatch_forge,
        stub_execute,
        capsys,
        monkeypatch,
    ):
        sig = _quota_paused_sig()
        stub_execute(sig)
        monkeypatch.setattr(
            "issuesmith.metrics_events.record_step_started",
            lambda paths: None,
        )

        rc = main(argv_factory(prompt_path, template_path))

        assert rc == 0
        _assert_wait_quota_stdout(capsys)

    def test_issue_number_parsed_from_variables(
        self, template_path, dispatch_forge, stub_execute, monkeypatch
    ):
        sig = _quota_paused_sig()
        stub_execute(sig)
        handle = MagicMock()
        monkeypatch.setattr(
            "issuesmith.ops.dispatch.handle_retry_signal",
            handle,
        )

        rc = main(
            [
                "run",
                "design",
                str(template_path),
                "issue_number=5802",
            ]
        )

        assert rc == 0
        handle.assert_called_once_with(sig, "run", 5802)

    def test_invalid_issue_number_becomes_none(
        self, template_path, dispatch_forge, stub_execute, monkeypatch
    ):
        sig = _quota_paused_sig()
        stub_execute(sig)
        handle = MagicMock()
        monkeypatch.setattr(
            "issuesmith.ops.dispatch.handle_retry_signal",
            handle,
        )

        rc = main(
            [
                "run",
                "design",
                str(template_path),
                "issue_number=not-an-int",
            ]
        )

        assert rc == 0
        handle.assert_called_once_with(sig, "run", None)

    def test_exec_has_no_issue_number(
        self, prompt_path, dispatch_forge, stub_execute, monkeypatch
    ):
        sig = _quota_paused_sig()
        stub_execute(sig)
        handle = MagicMock()
        monkeypatch.setattr(
            "issuesmith.ops.dispatch.handle_retry_signal",
            handle,
        )

        rc = main(["exec", "design", str(prompt_path)])

        assert rc == 0
        handle.assert_called_once_with(sig, "exec", None)


class TestEngineMainNonRetryErrors:
    def test_runtime_error_stays_rejected(
        self, template_path, stub_execute, capsys
    ):
        stub_execute(RuntimeError("ordinary failure"))

        rc = main(["run", "design", str(template_path)])

        assert rc == 1
        captured = capsys.readouterr()
        assert "REJECTED:ordinary failure" in captured.err
        assert "PIPELINE_STATUS: WAIT_QUOTA" not in captured.out
