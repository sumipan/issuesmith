"""dispatch requires: external gates, unmerged deps and forbidden paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests import test_dispatch_requires as _base
from tests.test_dispatch_requires import (
    RepairStepBoundary,
    _fail_gate,
    _make_ctx,
    _raised_andons,
    _RecordingForge,
    stand_in_repair_step,
)

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")

# ---------------------------------------------------------------------------
# nexus #4116: fail-closed external_leak target + requires observability
# ---------------------------------------------------------------------------


class TestExternalGateRequires:

    def test_build_requires_gates_unresolved_raises_gate_build_error(self, tmp_path, monkeypatch):
        from issuesmith.gates import GateBuildError
        from issuesmith.ops.dispatch import _build_requires_gates

        ref = "mypkg_gates_dispatch_fail:MISSING"
        (tmp_path / "mypkg_gates_dispatch_fail.py").write_text("pass\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        with pytest.raises(GateBuildError, match=ref):
            _build_requires_gates((ref,), {"worktree_path": str(tmp_path)})


class TestDepsUnmergedRequires:
    def test_unmerged_deferred_when_phase_has_deps_terminal(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="", requires=("deps",))
        gate = _fail_gate("deps.unmerged")
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"deps": gate},
            ),
            patch("issuesmith.ops.dispatch._deps_gated_by_admission", return_value=True),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "b1", _make_ctx())
        assert rc is None
        assert _raised_andons(forge) == []
        assert "deps.unmerged deferred to phase admission" in capsys.readouterr().err
        assert repair.launches == []

    def test_unmerged_raises_andon_when_not_gated_by_admission(self):
        from issuesmith.config import StepConfig
        from issuesmith.ops.dispatch import run_requires_loop

        cfg = StepConfig(module="", requires=("deps",))
        gate = _fail_gate("deps.unmerged")
        repair = RepairStepBoundary()
        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"deps": gate},
            ),
            patch("issuesmith.ops.dispatch._deps_gated_by_admission", return_value=False),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
            stand_in_repair_step(repair),
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "repair", _make_ctx())
        assert rc == 1
        assert _raised_andons(forge)[0].kind == "decision"
        assert repair.launches == []

class TestTargetUnknownNotRepairable:
    def test_is_violation_repairable_excludes_target_unknown(self):
        from issuesmith.ops.dispatch import _is_violation_repairable

        gates = {"external_leak": object()}
        assert _is_violation_repairable(
            "external_leak.target_unknown", gates, "p1"
        ) is False
        assert _is_violation_repairable(
            "external_leak.cjk_added_line", gates, "p1"
        ) is True


class TestFetchFreshIssueBodyLogging:

    def test_forge_failure_with_context_body_is_silent(self, capsys):
        from issuesmith.ops.dispatch import _fetch_fresh_issue_body

        forge = MagicMock()
        forge.issue_get.side_effect = RuntimeError("403")
        with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
            body = _fetch_fresh_issue_body({"issue_number": "7", "issue_body": "cached"})
        assert body == "cached"
        assert "issue body unavailable" not in capsys.readouterr().err

