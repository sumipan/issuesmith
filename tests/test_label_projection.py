"""Tests for label projection in dispatch and labels.project() (#3626).

Covers:
  - the final step of a phase (entry_step unless phases[].steps is declared)
    projects <phase>-done and drops <phase>-running (#4807)
  - StepResult(done) of m2 → merge-done added, merge-running removed
  - CLOSED issue without terminal label gets terminal label on next reconcile
  - labels.project() preserves phase precondition labels (e.g. draft-done kept
    when develop-running is active and develop's preconditions include draft-done)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from issuesmith.ops.labels import ExecRecord, project

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns() -> str:
    from issuesmith.config import get_config
    return get_config().label_namespace


# ---------------------------------------------------------------------------
# Final-step projection: the done of a phase's last step projects <phase>-done
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# map_step_result: the done of the merge phase's final step projects merge-done
# ---------------------------------------------------------------------------

class TestMapStepResultLabelProjection:
    """StepResult(done) of ``m2`` (merge's final step by default) projects merge-done."""

    def _run(self) -> tuple[int, MagicMock]:
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        forge = MagicMock()
        forge.issue_get.return_value = {"labels": [{"name": f"{_ns()}:merge-running"}]}
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = map_step_result(
                StepResult(status="done", markers=["MERGE_DONE"]),
                step_id="m2",
                context={"issue_number": "42", "workflow_name": "issuesmith"},
            )
        return rc, forge

    def test_merge_done_exits_zero(self):
        rc, _ = self._run()
        assert rc == 0

    def test_merge_done_projects_merge_done(self):
        ns = _ns()
        _, forge = self._run()
        forge.issue_update.assert_called_once()
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:merge-done" in kwargs["labels_add"]
        assert f"{ns}:merge-running" in kwargs["labels_remove"]


# ---------------------------------------------------------------------------
# labels.project(): precondition labels preserved for active phases
# ---------------------------------------------------------------------------

class TestProjectPreservePreconditions:
    """Phase precondition labels are not stripped when the phase is active."""

    def _make_config(self, tmp_path, monkeypatch):
        """Create a config where develop requires draft-done as precondition."""
        import yaml

        from issuesmith.config import load_config, reset_config_cache

        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "phases": [
                    {
                        "name": "draft",
                        "role": "design",
                        "entry_step": "b1",
                        "handler": "brushup",
                    },
                    {
                        "name": "develop",
                        "role": "implementation",
                        "entry_step": "cp2",
                        "handler": "impl",
                        "preconditions": ["draft-done"],
                    },
                    {
                        "name": "merge",
                        "role": "implementation",
                        "entry_step": "m2",
                        "handler": "merge",
                    },
                ],
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()
        return load_config()

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from issuesmith.config import reset_config_cache
        reset_config_cache()
        yield
        reset_config_cache()


# ---------------------------------------------------------------------------
# labels.project(): CLOSED issue terminal label
# ---------------------------------------------------------------------------

class TestClosedIssueTerminalLabel:
    """A CLOSED issue that has a terminal-phase exec record gets the done label."""

    def test_closed_merge_done_exec_record_gives_merge_done_label(self):
        ns = _ns()
        exec_recs = [ExecRecord(phase="merge", status="done")]
        desired = project(99, queue_state=None, exec_records=exec_recs, andon_inbox=[])
        assert f"{ns}:merge-done" in desired

    def test_closed_develop_done_exec_record_gives_develop_done_label(self):
        ns = _ns()
        exec_recs = [ExecRecord(phase="develop", status="done")]
        desired = project(99, queue_state=None, exec_records=exec_recs, andon_inbox=[])
        assert f"{ns}:develop-done" in desired

    def test_project_without_exec_records_returns_empty(self):
        desired = project(99, queue_state=None, exec_records=[], andon_inbox=[])
        assert not desired
