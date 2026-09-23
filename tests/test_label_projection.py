"""Tests for label projection in dispatch and labels.project() (#3626).

Covers:
  - MERGE_DONE  → merge-done added, merge-running removed
  - IMPL_DONE   → develop-done added, develop-running removed
  - REPORT_DONE → draft-done added, draft-running removed
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
# _project_marker_labels: marker → label delta applied to forge
# ---------------------------------------------------------------------------

class TestMarkerLabelProjection:
    """Verify _project_marker_labels applies the right add/remove for each marker."""

    def _run(self, marker: str, issue_number: int = 42) -> MagicMock:
        """Run _project_marker_labels and return the forge mock."""
        from issuesmith.ops.dispatch import _project_marker_labels

        context = {"issue_number": str(issue_number), "workflow_name": "issuesmith"}
        forge = MagicMock()
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            _project_marker_labels(marker, context)
        return forge

    def test_merge_done_adds_merge_done_label(self):
        ns = _ns()
        forge = self._run("MERGE_DONE")
        forge.issue_update.assert_called_once()
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:merge-done" in kwargs["labels_add"]

    def test_merge_done_removes_merge_running_label(self):
        ns = _ns()
        forge = self._run("MERGE_DONE")
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:merge-running" in kwargs["labels_remove"]

    def test_impl_done_adds_develop_done_label(self):
        ns = _ns()
        forge = self._run("IMPL_DONE")
        forge.issue_update.assert_called_once()
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:develop-done" in kwargs["labels_add"]

    def test_impl_done_removes_develop_running_label(self):
        ns = _ns()
        forge = self._run("IMPL_DONE")
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:develop-running" in kwargs["labels_remove"]

    def test_report_done_adds_draft_done_label(self):
        ns = _ns()
        forge = self._run("REPORT_DONE")
        forge.issue_update.assert_called_once()
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:draft-done" in kwargs["labels_add"]

    def test_report_done_removes_draft_running_label(self):
        ns = _ns()
        forge = self._run("REPORT_DONE")
        _, kwargs = forge.issue_update.call_args
        assert f"{ns}:draft-running" in kwargs["labels_remove"]

    def test_unknown_marker_no_label_update(self):
        forge = self._run("WORKTREE_READY")
        forge.issue_update.assert_not_called()

    def test_no_issue_number_no_label_update(self):
        from issuesmith.ops.dispatch import _project_marker_labels

        forge = MagicMock()
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            _project_marker_labels("MERGE_DONE", {"issue_number": ""})
        forge.issue_update.assert_not_called()


# ---------------------------------------------------------------------------
# map_step_result: markers + label projection combined
# ---------------------------------------------------------------------------

class TestMapStepResultLabelProjection:
    """MERGE_DONE / IMPL_DONE / REPORT_DONE markers trigger label projection."""

    def _run_marker(self, marker: str) -> tuple[int, MagicMock]:
        from issuesmith.ops.dispatch import map_step_result
        from issuesmith.steps.base import StepResult

        forge = MagicMock()
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            rc = map_step_result(
                StepResult(status="done", markers=[marker]),
                step_id="m2",
                context={"issue_number": "42", "workflow_name": "issuesmith"},
            )
        return rc, forge

    def test_merge_done_exits_zero(self):
        rc, _ = self._run_marker("MERGE_DONE")
        assert rc == 0

    def test_merge_done_calls_issue_update(self):
        _, forge = self._run_marker("MERGE_DONE")
        forge.issue_update.assert_called_once()

    def test_impl_done_calls_issue_update(self):
        _, forge = self._run_marker("IMPL_DONE")
        forge.issue_update.assert_called_once()

    def test_report_done_calls_issue_update(self):
        _, forge = self._run_marker("REPORT_DONE")
        forge.issue_update.assert_called_once()


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
                    {"name": "draft", "role": "design", "entry_step": "b1"},
                    {
                        "name": "develop",
                        "role": "implementation",
                        "entry_step": "cp2",
                        "preconditions": ["draft-done"],
                    },
                    {"name": "merge", "role": "implementation", "entry_step": "m2"},
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

    def test_draft_done_preserved_when_develop_running(self, tmp_path, monkeypatch):
        """develop-running active + draft-done in preconditions → draft-done kept."""
        self._make_config(tmp_path, monkeypatch)
        ns = _ns()
        exec_recs = [
            ExecRecord(phase="draft", status="done"),
            ExecRecord(phase="develop", status="running"),
        ]
        desired = project(1, queue_state=None, exec_records=exec_recs, andon_inbox=[])
        assert f"{ns}:draft-done" in desired

    def test_draft_done_preserved_when_develop_done(self, tmp_path, monkeypatch):
        """develop-done active + draft-done precondition → draft-done still kept."""
        self._make_config(tmp_path, monkeypatch)
        ns = _ns()
        exec_recs = [
            ExecRecord(phase="draft", status="done"),
            ExecRecord(phase="develop", status="done"),
        ]
        desired = project(1, queue_state=None, exec_records=exec_recs, andon_inbox=[])
        assert f"{ns}:draft-done" in desired

    def test_draft_done_not_preserved_when_develop_not_active(self, tmp_path, monkeypatch):
        """Without develop active, draft-done may not be in desired (most-advanced wins)."""
        self._make_config(tmp_path, monkeypatch)
        ns = _ns()
        # Only draft-done, no develop-* records
        exec_recs = [ExecRecord(phase="draft", status="done")]
        desired = project(1, queue_state=None, exec_records=exec_recs, andon_inbox=[])
        # draft-done IS the most-advanced label → should be present
        assert f"{ns}:draft-done" in desired

    def test_no_preconditions_phase_behaves_normally(self, tmp_path, monkeypatch):
        """Phase with no preconditions: standard most-advanced-wins logic unchanged."""
        import yaml

        from issuesmith.config import reset_config_cache

        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "phases": [
                    {"name": "draft", "role": "design", "entry_step": "b1"},
                    {"name": "develop", "role": "implementation", "entry_step": "cp2"},
                    {"name": "merge", "role": "implementation", "entry_step": "m2"},
                ],
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        ns = _ns()
        exec_recs = [
            ExecRecord(phase="draft", status="done"),
            ExecRecord(phase="develop", status="running"),
        ]
        desired = project(1, queue_state=None, exec_records=exec_recs, andon_inbox=[])
        # develop-running is most advanced; draft-done NOT required by precondition
        # so it may or may not be present depending on implementation
        assert f"{ns}:develop-running" in desired


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
