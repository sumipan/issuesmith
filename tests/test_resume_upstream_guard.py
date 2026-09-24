"""Upstream guard tests for resume --from (#3636).

Fixtures use real exec.jsonl / done dir to verify the guard without mocking
_load_step_statuses. Only _run_ghdag_recover is mocked to avoid subprocess calls.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import issuesmith.config as config_module
from issuesmith.config import _build_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IKEY = "issuesmith:impl:3627"
_ISSUE = 3627
_HANDLER = "impl"
_WORKFLOW = "issuesmith"

# p1 -> p2 -> p3 serial chain
_STEPS = [
    {
        "uuid": "p1-uuid",
        "command": "bash p1",
        "idempotency_key": _IKEY,
        "annotations": {"step_name": "p1"},
        "depends": [],
        "result_path": "jobs/p1-result.md",
    },
    {
        "uuid": "p2-uuid",
        "command": "bash p2",
        "idempotency_key": _IKEY,
        "annotations": {"step_name": "p2"},
        "depends": ["p1-uuid"],
        "result_path": "jobs/p2-result.md",
    },
    {
        "uuid": "p3-uuid",
        "command": "bash p3",
        "idempotency_key": _IKEY,
        "annotations": {"step_name": "p3"},
        "depends": ["p2-uuid"],
        "result_path": "jobs/p3-result.md",
    },
]


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_exec_jsonl(jobs: Path, records: list[dict]) -> None:
    path = jobs / "exec.jsonl"
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _make_done(done_dir: Path, uuid: str, content: str) -> None:
    done_dir.mkdir(parents=True, exist_ok=True)
    (done_dir / uuid).write_text(content, encoding="utf-8")


@pytest.fixture
def pipeline_fs(tmp_path: Path):
    """p1->p2->p3 chain; caller sets done markers as needed."""
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (jobs / "done").mkdir()
    (jobs / "running").mkdir()
    (tmp_path / ".pipeline-state").mkdir()

    _write_exec_jsonl(jobs, _STEPS)

    cfg = _build_config(
        {"repo": "sumipan/issuesmith", "workflow": "issuesmith.yaml"},
        root=tmp_path,
    )
    yield tmp_path, cfg
    config_module.reset_config_cache()


def _run_resume(cfg, capsys, **kwargs):
    """Call resume() with mocked config and ghdag recover."""
    from issuesmith.resume import resume

    with (
        patch("issuesmith.resume.get_config", return_value=cfg),
        patch("issuesmith.resume._run_ghdag_recover", return_value=0) as mock_recover,
        patch("issuesmith.resume.QueueStore") as mock_store_cls,
    ):
        mock_store = mock_store_cls.return_value
        mock_store.remove_in_flight.return_value = None
        rc = resume(_ISSUE, **kwargs)
    captured = capsys.readouterr()
    return rc, captured, mock_recover


# ---------------------------------------------------------------------------
# _upstream_blockers (pure function, no I/O)
# ---------------------------------------------------------------------------

def _make_step(step_name, uuid, depends, status):
    from ghdag.status import StepStatus
    return StepStatus(
        uuid=uuid,
        step_name=step_name,
        depends=depends,
        status=status,
        started_at=None,
        elapsed_sec=None,
        result_path=None,
    )


class TestUpstreamBlockers:
    def _call(self, steps, from_step):
        from issuesmith.resume import _upstream_blockers
        return _upstream_blockers(steps, from_step)

    def test_empty_steps_returns_empty(self):
        assert self._call([], "p2") == []

    def test_from_step_not_found_returns_empty(self):
        p1 = _make_step("p1", "p1-uuid", [], "failed")
        assert self._call([p1], "missing") == []

    def test_no_upstream_returns_empty(self):
        p1 = _make_step("p1", "p1-uuid", [], "failed")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "dep_failed")
        assert self._call([p1, p2], "p1") == []

    def test_success_upstream_not_blocked(self):
        p1 = _make_step("p1", "p1-uuid", [], "success")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "pending")
        assert self._call([p1, p2], "p2") == []

    def test_pending_upstream_not_blocked(self):
        p1 = _make_step("p1", "p1-uuid", [], "pending")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "pending")
        assert self._call([p1, p2], "p2") == []

    def test_running_upstream_not_blocked(self):
        p1 = _make_step("p1", "p1-uuid", [], "running")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "dep_failed")
        assert self._call([p1, p2], "p2") == []

    def test_failed_upstream_is_blocker(self):
        p1 = _make_step("p1", "p1-uuid", [], "failed")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "dep_failed")
        blockers = self._call([p1, p2], "p2")
        assert len(blockers) == 1
        assert blockers[0].step_name == "p1"

    def test_cancelled_upstream_is_blocker(self):
        p1 = _make_step("p1", "p1-uuid", [], "cancelled")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "dep_failed")
        blockers = self._call([p1, p2], "p2")
        assert len(blockers) == 1
        assert blockers[0].step_name == "p1"

    def test_topo_order_upstream_first(self):
        """p1(failed) -> p2(dep_failed) -> p3; from=p3 gives [p1, p2]."""
        p1 = _make_step("p1", "p1-uuid", [], "failed")
        p2 = _make_step("p2", "p2-uuid", ["p1-uuid"], "dep_failed")
        p3 = _make_step("p3", "p3-uuid", ["p2-uuid"], "dep_failed")
        blockers = self._call([p1, p2, p3], "p3")
        assert [b.step_name for b in blockers] == ["p1", "p2"]


# ---------------------------------------------------------------------------
# AC-1: p1 failed, p2 dep_failed; resume from p2 -> exit 1, no recover call
# ---------------------------------------------------------------------------

class TestAC1:
    def test_rejects_with_failed_upstream(self, pipeline_fs, capsys):
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="p2")

        assert rc == 1
        assert mock_recover.call_count == 0
        assert "p1 (failed)" in captured.err
        assert "--from p1" in captured.err
        assert "--from p2 --mark-done p1" in captured.err

    def test_ac1b_from_p3_shows_both_blockers(self, pipeline_fs, capsys):
        """p1 failed, p2 dep_failed, from=p3: blockers show p1 and p2."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="p3")

        assert rc == 1
        assert mock_recover.call_count == 0
        assert "p1 (failed)" in captured.err
        assert "p2 (dep_failed)" in captured.err
        assert "--from p1" in captured.err
        assert "--from p2 --mark-done p1" in captured.err

    def test_ac1c_cancelled_upstream_also_rejected(self, pipeline_fs, capsys):
        """p1 CANCELLED -> resume from p2 is also rejected."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "CANCELLED")

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="p2")

        assert rc == 1
        assert mock_recover.call_count == 0
        assert "p1 (cancelled)" in captured.err


# ---------------------------------------------------------------------------
# AC-2: --mark-done p1 writes done marker and calls recover
# ---------------------------------------------------------------------------

class TestAC2:
    def test_mark_done_writes_marker_and_calls_recover(self, pipeline_fs, capsys):
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        done_file = tmp / "jobs" / "done" / "p1-uuid"
        assert done_file.read_text() == "TIMEOUT"

        rc, captured, mock_recover = _run_resume(
            cfg, capsys, from_step="p2", mark_done=["p1"]
        )

        assert rc == 0
        assert done_file.read_text() == "0"
        mock_recover.assert_called_once()
        assert "marked done: p1" in captured.err

    def test_ac2b_mark_done_dep_failed_exits_2(self, pipeline_fs, capsys):
        """--mark-done p2 (dep_failed) returns 2, no marker written."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        rc, captured, mock_recover = _run_resume(
            cfg, capsys, from_step="p2", mark_done=["p2"]
        )

        assert rc == 2
        assert mock_recover.call_count == 0
        assert (tmp / "jobs" / "done" / "p2-uuid").exists() is False

    def test_ac2b_mark_done_downstream_exits_2(self, pipeline_fs, capsys):
        """--mark-done p3 (downstream of from=p2) returns 2, no marker written."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        rc, captured, mock_recover = _run_resume(
            cfg, capsys, from_step="p2", mark_done=["p3"]
        )

        assert rc == 2
        assert mock_recover.call_count == 0

    def test_ac2c_mark_done_p1_but_p2_still_blocks_from_p3(self, pipeline_fs, capsys):
        """from=p3 --mark-done p1: p2 dep_failed remains, exit 1, no marker written."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        p1_done = tmp / "jobs" / "done" / "p1-uuid"
        original_content = p1_done.read_text()

        rc, captured, mock_recover = _run_resume(
            cfg, capsys, from_step="p3", mark_done=["p1"]
        )

        assert rc == 1
        assert mock_recover.call_count == 0
        assert p1_done.read_text() == original_content

    def test_ac2d_warns_when_result_file_missing(self, pipeline_fs, capsys):
        """--mark-done p1 when p1 has no result file: warning on stderr, proceeds."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "TIMEOUT")

        rc, captured, mock_recover = _run_resume(
            cfg, capsys, from_step="p2", mark_done=["p1"]
        )

        assert rc == 0
        assert mock_recover.call_count == 1
        assert "warning: result file" in captured.err


# ---------------------------------------------------------------------------
# AC-2e: CLI --mark-done without --from exits 2
# ---------------------------------------------------------------------------

class TestAC2e:
    def test_mark_done_without_from_exits_2(self):
        from issuesmith.cli import _cmd_resume
        with pytest.raises(SystemExit) as exc_info:
            _cmd_resume([str(_ISSUE), "--mark-done", "p1"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# AC-3: all upstream success -> guard passes through to recover
# ---------------------------------------------------------------------------

class TestAC3:
    def test_all_success_calls_recover(self, pipeline_fs, capsys):
        """p1 success, p2 pending: resume from p2 calls recover once."""
        tmp, cfg = pipeline_fs
        _make_done(tmp / "jobs" / "done", "p1-uuid", "0")

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="p2")

        assert rc == 0
        mock_recover.assert_called_once()

    def test_running_upstream_calls_recover(self, pipeline_fs, capsys):
        """p1 running (in running dir): resume from p2 passes through."""
        tmp, cfg = pipeline_fs
        (tmp / "jobs" / "running" / "p1-uuid.json").write_text(
            json.dumps({"started_at": "2026-09-01T12:00:00"}), encoding="utf-8"
        )

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="p2")

        assert rc == 0
        mock_recover.assert_called_once()

    def test_no_steps_passes_through(self, tmp_path, capsys):
        """exec.jsonl absent -> no steps -> guard passes through."""
        (tmp_path / "jobs").mkdir()
        (tmp_path / "jobs" / "done").mkdir()
        (tmp_path / "jobs" / "running").mkdir()
        (tmp_path / ".pipeline-state").mkdir()
        cfg = _build_config({"repo": "sumipan/issuesmith", "workflow": "issuesmith.yaml"}, root=tmp_path)

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="p2")

        assert rc == 0
        mock_recover.assert_called_once()
        config_module.reset_config_cache()


# ---------------------------------------------------------------------------
# AC-4: from_step not in steps -> pass through
# ---------------------------------------------------------------------------

class TestAC4:
    def test_unknown_from_step_passes_through(self, pipeline_fs, capsys):
        """from_step not found in steps -> no blockers -> recover called."""
        tmp, cfg = pipeline_fs

        rc, captured, mock_recover = _run_resume(cfg, capsys, from_step="nonexistent")

        assert rc == 0
        mock_recover.assert_called_once()


# ---------------------------------------------------------------------------
# AC-5: tests do not read/write outside tmp_path (enforced by no_side_effects fixture)
# ---------------------------------------------------------------------------
