"""resume --from infers the handler from exec.jsonl (sumipan/nexus#3844).

nexus runs M1 / M2 inside the impl DAG, but the workflow YAML table maps them to the
merge handler, so ``resume 3762 --from m1`` reset 0 steps (2026-09-25).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import issuesmith.config as config_module
from issuesmith.config import _build_config

_ISSUE = 3762
_WORKFLOW = "issuesmith"
_CHAIN = ["p0", "p1", "p2", "p3", "cp2", "m1", "m2"]


def _impl_records(issue: int, key_suffix: str = "") -> list[dict]:
    key = f"issuesmith:impl:{issue}{key_suffix}"
    records = []
    prev: str | None = None
    for name in _CHAIN:
        uuid = f"{name}-uuid"
        records.append({
            "uuid": uuid,
            "command": f"bash {name}",
            "idempotency_key": key,
            "annotations": {"step_name": name},
            "depends": [prev] if prev else [],
            "result_path": f"jobs/{name}-result.md",
        })
        prev = uuid
    return records


def _fs(tmp_path: Path, records: list[dict]):
    jobs = tmp_path / "jobs"
    (jobs / "done").mkdir(parents=True)
    (jobs / "running").mkdir()
    (tmp_path / ".pipeline-state").mkdir()
    (jobs / "exec.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    for rec in records:
        (jobs / f"20260925000000-shell-order-{rec['uuid']}.md").write_text(
            rec["command"], encoding="utf-8"
        )
    cfg = _build_config({"repo": "sumipan/nexus", "workflow": "issuesmith.yaml"}, root=tmp_path)
    return jobs, cfg


@pytest.fixture(autouse=True)
def _reset_cache():
    yield
    config_module.reset_config_cache()


def _m1_failed(jobs: Path) -> None:
    for name in _CHAIN[:5]:
        (jobs / "done" / f"{name}-uuid").write_text("0", encoding="utf-8")
    (jobs / "done" / "m1-uuid").write_text("FAILED", encoding="utf-8")
    (jobs / "done" / "m2-uuid").write_text("DEP_FAILED", encoding="utf-8")


def _run(cfg, jobs: Path, recover_calls: list, *, table: str = "merge", **kwargs) -> int:
    from ghdag.dag.recover import execute_recover, plan_recover

    from issuesmith.resume import resume

    def _in_process_recover(issue, handler, from_step, workflow=None):
        recover_calls.append((issue, handler, from_step, workflow))
        plan = plan_recover(
            state_dir=cfg.root / ".pipeline-state",
            exec_jsonl_path=jobs / "exec.jsonl",
            workflow_name=workflow,
            handler_name=handler,
            issue_number=issue,
            queue_dir=jobs,
            done_dir=jobs / "done",
            from_step=from_step,
        )
        execute_recover(plan, queue_dir=jobs, done_dir=jobs / "done")
        return 0

    with (
        patch("issuesmith.resume.get_config", return_value=cfg),
        patch("issuesmith.resume.handler_for_failed_step", return_value=table),
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._run_ghdag_recover", side_effect=_in_process_recover),
        patch("issuesmith.resume._restore_running_state"),
        patch("issuesmith.resume.QueueStore"),
    ):
        return resume(_ISSUE, workflow=_WORKFLOW, **kwargs)


def _statuses(cfg, jobs: Path, handler: str) -> dict[str, str]:
    from ghdag import status as ghdag_status

    steps = ghdag_status.issue_status(
        _ISSUE,
        handler=handler,
        workflow=_WORKFLOW,
        exec_jsonl_path=jobs / "exec.jsonl",
        state_dir=cfg.root / ".pipeline-state",
        done_dir=jobs / "done",
    ).steps
    return {s.step_name: s.status for s in steps}


# ---------------------------------------------------------------------------
# _infer_handler_from_exec
# ---------------------------------------------------------------------------

class TestInferHandlerFromExec:
    def _infer(self, cfg, issue: int, step: str):
        from issuesmith.resume import _infer_handler_from_exec

        with patch("issuesmith.resume.get_config", return_value=cfg):
            return _infer_handler_from_exec(issue, step)

    def test_matches_step_name_of_issue(self, tmp_path):
        _jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
        assert self._infer(cfg, _ISSUE, "m1") == "impl"

    def test_generation_suffixed_key(self, tmp_path):
        _jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE, ":1"))
        assert self._infer(cfg, _ISSUE, "m2") == "impl"

    def test_other_issue_is_ignored(self, tmp_path):
        _jobs, cfg = _fs(tmp_path, _impl_records(37620))
        assert self._infer(cfg, _ISSUE, "m1") is None

    def test_unknown_step_returns_none(self, tmp_path):
        _jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
        assert self._infer(cfg, _ISSUE, "d1") is None

    def test_missing_exec_jsonl_returns_none(self, tmp_path):
        jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
        (jobs / "exec.jsonl").unlink()
        assert self._infer(cfg, _ISSUE, "m1") is None

    def test_malformed_lines_are_skipped(self, tmp_path):
        jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
        text = (jobs / "exec.jsonl").read_text(encoding="utf-8")
        (jobs / "exec.jsonl").write_text("{not json\n[1]\n" + text, encoding="utf-8")
        assert self._infer(cfg, _ISSUE, "m1") == "impl"


# ---------------------------------------------------------------------------
# AC-1 / AC-5: resume --from m1 without --handler resets m1 and m2 of the impl DAG
# ---------------------------------------------------------------------------

def test_ac1_from_m1_infers_impl_and_resets_two_steps(tmp_path, capsys):
    jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
    _m1_failed(jobs)
    calls: list = []

    rc = _run(cfg, jobs, calls, from_step="m1")

    assert rc == 0
    assert calls == [(_ISSUE, "impl", "m1", _WORKFLOW)]
    after = _statuses(cfg, jobs, "impl")
    assert after["m1"] == "pending"
    assert after["m2"] == "pending"
    assert all(after[name] == "success" for name in _CHAIN[:5])


def test_ac5_logs_when_inferred_differs_from_table(tmp_path, capsys):
    jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
    _m1_failed(jobs)

    _run(cfg, jobs, [], from_step="m1", table="merge")

    assert "handler inferred from exec: impl (table said merge)" in capsys.readouterr().err


def test_no_log_when_inferred_matches_table(tmp_path, capsys):
    jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
    _m1_failed(jobs)

    _run(cfg, jobs, [], from_step="m1", table="impl")

    assert "handler inferred from exec" not in capsys.readouterr().err


def test_explicit_handler_wins_over_exec(tmp_path, capsys):
    jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
    _m1_failed(jobs)
    calls: list = []

    with patch("issuesmith.resume._infer_handler_from_exec") as mock_infer:
        rc = _run(cfg, jobs, calls, from_step="m1", handler="impl")

    assert rc == 0
    mock_infer.assert_not_called()
    assert calls[0][1] == "impl"


# ---------------------------------------------------------------------------
# AC-2: no exec record for the issue -> static table
# ---------------------------------------------------------------------------

def test_ac2_falls_back_to_static_table(tmp_path, capsys):
    jobs, cfg = _fs(tmp_path, _impl_records(4000))
    calls: list = []

    with patch("issuesmith.resume._load_step_statuses", return_value=[]) as mock_load:
        rc = _run(cfg, jobs, calls, from_step="m1", table="merge")

    assert rc == 0
    assert mock_load.call_args[0][1] == "merge"
    assert calls[0][1] == "merge"
    assert "handler inferred from exec" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# AC-3: the handler's DAG has no such step -> exit 1 before ghdag recover
# ---------------------------------------------------------------------------

def test_ac3_step_missing_from_handler_dag_exits_1(tmp_path, capsys):
    jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
    _m1_failed(jobs)
    calls: list = []

    rc = _run(cfg, jobs, calls, from_step="m1", handler="merge")

    assert rc == 1
    assert calls == []
    err = capsys.readouterr().err
    assert "no steps to reset: step m1 not found in handler merge (try --handler impl)" in err


def test_ac3_fallback_handler_without_run_exits_1(tmp_path, capsys):
    """exec knows the issue but not the step; the table's handler has no run -> exit 1."""
    jobs, cfg = _fs(tmp_path, _impl_records(_ISSUE))
    _m1_failed(jobs)
    calls: list = []

    rc = _run(cfg, jobs, calls, from_step="d1", table="draft")

    assert rc == 1
    assert calls == []
    assert (
        "no steps to reset: step d1 not found in handler draft (try --handler …)"
        in capsys.readouterr().err
    )


def test_issue_without_exec_records_still_calls_recover(tmp_path, capsys):
    """No exec record for the issue: nothing to judge by, recover runs as before."""
    jobs, cfg = _fs(tmp_path, _impl_records(4000))
    calls: list = []

    rc = _run(cfg, jobs, calls, from_step="m1", table="merge")

    assert rc == 0
    assert calls == [(_ISSUE, "merge", "m1", _WORKFLOW)]
    assert "no steps to reset" not in capsys.readouterr().err
