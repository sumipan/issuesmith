"""Tests for per-step repair.max in run_requires_loop (#4986, #5787)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ghdag.workflow.gates import Violation

from issuesmith.config import RepairConfig, StepConfig
from issuesmith.ops.dispatch import run_requires_loop


def _v(
    rule_id: str = "lint.E501",
    *,
    location: str | None = None,
    message: str = "line too long",
) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity="fail",
        message=message,
        location=location,
        auto_fixable=False,
        fix_hint="wrap",
    )


def _ctx(tmp_path) -> dict[str, str]:
    return {
        "issue_number": "1",
        "workflow_name": "issuesmith",
        "base_branch": "main",
        "worktree_path": str(tmp_path),
        "allow_paths": "- src/**",
    }


def _fail_gate() -> MagicMock:
    gate = MagicMock()
    gate.check.return_value = [_v()]
    return gate


def test_repair_max_one_andon_after_single_repair(tmp_path) -> None:
    cfg = StepConfig(
        requires=("lint",),
        input_kind="worktree",
        requires_declared=True,
        repair=RepairConfig(max=1),
    )
    repair_calls: list[int] = []

    def fake_repair(*_args, **_kwargs):
        repair_calls.append(1)
        return None

    with (
        patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            return_value={"lint": _fail_gate()},
        ),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", side_effect=fake_repair),
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(cfg, "p1", _ctx(tmp_path))

    assert rc == 1
    assert len(repair_calls) == 1
    andon.assert_called_once()
    assert andon.call_args[0][1].kind == "decision"


def test_repair_default_max_is_three() -> None:
    assert StepConfig().repair.max == 3


def test_proper_subset_allows_four_repairs_with_max_three(tmp_path) -> None:
    cfg = StepConfig(requires=("lint",), repair=RepairConfig(max=3))
    a, b, c, d = (
        _v(message="a", location="a.py"),
        _v(message="b", location="b.py"),
        _v(message="c", location="c.py"),
        _v(message="d", location="d.py"),
    )
    gate = MagicMock()
    gate.check.side_effect = [[a, b, c, d], [a, b, c], [a, b], [a], []]

    with (
        patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", return_value=None) as repair,
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(cfg, "p1", _ctx(tmp_path))

    assert rc is None
    assert repair.call_count == 4
    andon.assert_not_called()


def test_same_count_swap_hits_no_progress_limit(tmp_path) -> None:
    cfg = StepConfig(requires=("lint",), repair=RepairConfig(max=3))
    gate = MagicMock()
    gate.check.side_effect = [
        [_v(message="a", location="a.py")],
        [_v(message="b", location="b.py")],
        [_v(message="c", location="c.py")],
        [_v(message="d", location="d.py")],
    ]

    with (
        patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", return_value=None) as repair,
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(cfg, "p1", _ctx(tmp_path))

    assert rc == 1
    assert repair.call_count == 3
    summary = andon.call_args[0][1].summary
    assert "repair oscillation" not in summary
    assert "requires evaluation failed after 3 repair(s)" in summary
    assert "repairs=3" in summary
    assert "no_progress=3" in summary


def test_oscillation_before_no_progress_limit(tmp_path) -> None:
    cfg = StepConfig(requires=("lint",), repair=RepairConfig(max=3))
    violation = _v(message="same")
    gate = MagicMock()
    gate.check.side_effect = [[violation], [violation]]

    with (
        patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", return_value=None),
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(cfg, "p1", _ctx(tmp_path))

    assert rc == 1
    assert "repair oscillation detected" in andon.call_args[0][1].summary


def test_safety_valve_total_repairs(tmp_path) -> None:
    cfg = StepConfig(requires=("lint",), repair=RepairConfig(max=2))
    prev = _v(message="old", location="old.py")
    gate = MagicMock()
    gate.check.return_value = [_v(message="new", location="new.py")]

    with (
        patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", return_value=None) as repair,
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(
            cfg,
            "p1",
            _ctx(tmp_path),
            repair_count=1,
            _total_repair_count=5,
            _initial_blocking_count=3,
            _prev_blocking_fingerprints=frozenset(
                {("lint.E501", prev.location or "", prev.message)}
            ),
        )

    assert rc == 1
    repair.assert_not_called()
    summary = andon.call_args[0][1].summary
    assert "repairs=5" in summary
    assert "requires evaluation failed" in summary


def test_repair_step_failure_does_not_increment_counts(tmp_path) -> None:
    cfg = StepConfig(requires=("lint",), repair=RepairConfig(max=3))
    gate = MagicMock()
    gate.check.return_value = [_v()]

    with (
        patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"lint": gate}),
        patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
        patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        patch("issuesmith.ops.dispatch._get_preexisting_rule_ids", return_value=frozenset()),
        patch("issuesmith.ops.dispatch._run_repair_step", return_value=1),
        patch("issuesmith.ops.dispatch._raise_andon") as andon,
        patch("issuesmith.ops.dispatch.get_forge"),
    ):
        rc = run_requires_loop(cfg, "p1", _ctx(tmp_path))

    assert rc == 1
    andon.assert_not_called()
