"""engine CLI: RetrySignal -> WAIT_QUOTA + exit 0 (sumipan/nexus#5802)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from ghdag.core.reply_block import parse_reply_block

from issuesmith.engine import RetryReason, RetrySignal, main

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _quota_paused_sig(*, role: str = "design") -> RetrySignal:
    after = _T0 + timedelta(hours=1)
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
    block = parse_reply_block(captured.out)
    assert block is not None
    assert block.status == "wait_quota"
    assert block.engines
    assert block.after is not None and "+" in block.after
    assert "PIPELINE_STATUS:" not in captured.out
    assert not any(ln.startswith("WAIT_QUOTA:") for ln in captured.out.splitlines())


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
