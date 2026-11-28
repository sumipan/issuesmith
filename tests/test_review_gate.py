"""Tests for gates.review.ReviewGate (#4986)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.config import ReviewConfig, TierConfig
from issuesmith.contract import RetryReason, RetrySignal
from issuesmith.engine import (
    _ENGINE_TRANSIENT_PAUSE_SEC,
    _GUARDED_TRANSIENT_MAX,
    _EngineProcess,
)
from issuesmith.gates import GateBuildContext, GateBuildError
from issuesmith.gates.review import (
    ReviewGate,
    _worktree_diff_lines,
    build_review_gate,
)
from issuesmith.pr_scope import DIFF_LINES_FALLBACK
from issuesmith.repair import evaluate_requires


def _review_cfg() -> ReviewConfig:
    return ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
    )


def test_check_pass_returns_empty(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    with patch(
        "issuesmith.engine.run_guarded_output",
        return_value=(0, "ok\nPIPELINE_STATUS: CP2_PASS\n"),
    ):
        assert gate.check("", []) == []


def test_check_propagates_retry_signal(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
    with patch("issuesmith.engine.run_guarded_output", side_effect=sig):
        with pytest.raises(RetrySignal):
            gate.check("", [])


def test_check_propagates_engine_transient_retry_signal(tmp_path: Path) -> None:
    """Refs #7397 — review gate forwards guarded-order ENGINE_TRANSIENT retries."""
    gate = ReviewGate(_review_cfg(), "x", tmp_path, {})
    sig = RetrySignal(reason=RetryReason.ENGINE_TRANSIENT)
    with patch("issuesmith.engine.run_guarded_output", side_effect=sig):
        with pytest.raises(RetrySignal) as exc_info:
            gate.check("", [])
    assert exc_info.value.reason is RetryReason.ENGINE_TRANSIENT


_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _T0 if tz is None else _T0.astimezone(tz)


def _patch_review_template_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "cp2.md").write_text("order", encoding="utf-8")
    cfg = MagicMock()
    cfg.paths.template_dir = tmp_path
    monkeypatch.setattr("issuesmith.gates.review.get_config", lambda: cfg)


def test_check_empty_output_raises_engine_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC1 — exit 0 with empty stdout schedules ENGINE_TRANSIENT via review."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE",
        tmp_path / "engine-transient-retries.json",
    )
    monkeypatch.setenv("GHDAG_TASK_UUID", "review-empty-out")
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: quota_gate)
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    with pytest.raises(RetrySignal) as exc_info:
        gate.check("", [])
    assert exc_info.value.reason is RetryReason.ENGINE_TRANSIENT
    assert exc_info.value.after == _T0 + timedelta(seconds=_ENGINE_TRANSIENT_PAUSE_SEC)
    report_kwargs = quota_gate.report.call_args.kwargs
    assert report_kwargs["engine"] == "codex"
    assert report_kwargs["status"] == "paused"
    assert report_kwargs["reason"] == "engine_transient: empty_output"


def test_check_unclassified_failure_raises_engine_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC2 — unclassified non-zero exit schedules ENGINE_TRANSIENT."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE",
        tmp_path / "engine-transient-retries.json",
    )
    monkeypatch.setenv("GHDAG_TASK_UUID", "review-unclassified")
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    quota_gate = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: quota_gate)
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    with pytest.raises(RetrySignal):
        gate.check("", [])
    assert (
        quota_gate.report.call_args.kwargs["reason"]
        == "engine_transient: unclassified_failure"
    )


def test_check_transient_over_limit_returns_engine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC3 — after retry cap, review maps empty output to engine_error."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    state_path = tmp_path / "engine-transient-retries.json"
    monkeypatch.setattr("issuesmith.engine._GUARDED_TRANSIENT_STATE_FILE", state_path)
    task_uuid = "review-over-limit"
    monkeypatch.setenv("GHDAG_TASK_UUID", task_uuid)
    monkeypatch.setattr("issuesmith.engine.datetime", _FixedDatetime)
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: MagicMock())
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=0,
            stdout="",
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    for _ in range(_GUARDED_TRANSIENT_MAX):
        with pytest.raises(RetrySignal):
            gate.check("", [])
    vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert vs[0].message == "review failed without output"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert f"{task_uuid}:cp2.md" not in state


def test_check_failure_marker_still_returns_review_violation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC4 — structured CP2_FAIL output stays review.cp2, not engine_error."""
    _patch_review_template_dir(monkeypatch, tmp_path)
    stdout = (
        "```ghdag\n"
        "status: failed\n"
        "code: CP2_FAIL\n"
        "problems:\n"
        "  - bad thing\n"
        "```\n"
    )
    monkeypatch.setattr(
        "issuesmith.engine._execute",
        lambda *args, **kwargs: _EngineProcess(
            args=[],
            returncode=0,
            stdout=stdout,
            stderr="",
            failure_class=None,
            engine_used="codex",
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._render_template", lambda *args, **kwargs: "order"
    )
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2"
    assert vs[0].message == "bad thing"


def test_check_issue_body_fetch_failure_still_returns_engine_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs #7397 AC5 — issue body fetch failure is engine_error, not transient retry."""
    template = tmp_path / "cp2.md"
    template.write_text("$issue_body_file\n", encoding="utf-8")
    cfg = MagicMock()
    cfg.paths.template_dir = tmp_path
    monkeypatch.setattr("issuesmith.gates.review.get_config", lambda: cfg)
    monkeypatch.setattr(
        "issuesmith.ops.dispatch.fetch_issue_inputs",
        lambda parsed: ("", []),
    )
    execute = MagicMock()
    monkeypatch.setattr("issuesmith.engine._execute", execute)
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {"issue_number": "99"})
    vs = gate.check("", [])
    execute.assert_not_called()
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"


def test_evaluate_requires_propagates_retry_signal() -> None:
    gate = MagicMock()
    gate.check.side_effect = RetrySignal(reason=RetryReason.QUOTA_PAUSED)
    with pytest.raises(RetrySignal):
        evaluate_requires({"review": gate}, "", [])


def test_evaluate_requires_gate_error_on_value_error() -> None:
    gate = MagicMock()
    gate.check.side_effect = ValueError("boom")
    result = evaluate_requires({"review": gate}, "", [])
    assert result.gate_error is not None


def test_build_review_gate_missing_step_id(tmp_path: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=tmp_path,
        allow_paths=[],
        base_branch="main",
    )
    with pytest.raises(GateBuildError):
        build_review_gate(ctx)


def test_build_review_gate_missing_review_config(tmp_path: Path) -> None:
    ctx = GateBuildContext(
        worktree_path=tmp_path,
        allow_paths=[],
        base_branch="main",
        step_id="missing",
    )
    with pytest.raises(GateBuildError):
        build_review_gate(ctx)


_FILL_LINE = (
    "[issuesmith-review] step=cp2 optional variables missing from order, "
    "filled empty: execution_constraints"
)


def _optional_cfg(*names: str) -> ReviewConfig:
    return ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
        optional_variables=names,
    )


def _run_check(gate: ReviewGate) -> list[str]:
    with patch(
        "issuesmith.engine.run_guarded_output",
        return_value=(0, "PIPELINE_STATUS: CP2_PASS\n"),
    ) as mock_run:
        assert gate.check("", []) == []
    return mock_run.call_args.args[2]


def test_check_does_not_override_passed_optional_variable(
    tmp_path: Path, capsys
) -> None:
    gate = ReviewGate(
        _optional_cfg("execution_constraints"),
        "cp2",
        tmp_path,
        {"issue_number": "1", "execution_constraints": "abc"},
    )
    variables = _run_check(gate)
    assert "execution_constraints=abc" in variables
    assert "execution_constraints=" not in variables
    assert "optional variables missing" not in capsys.readouterr().err


def test_check_does_not_fill_unlisted_variable(tmp_path: Path) -> None:
    gate = ReviewGate(
        _optional_cfg("execution_constraints"), "cp2", tmp_path, {"issue_number": "1"}
    )
    variables = _run_check(gate)
    assert not any(v.startswith("other_var=") for v in variables)


def test_check_without_optional_variables_unchanged(tmp_path: Path, capsys) -> None:
    passed = {"issue_number": "1", "worktree": "w"}
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, passed)
    variables = _run_check(gate)
    assert variables == [f"{k}={v}" for k, v in passed.items()]
    assert "optional variables missing" not in capsys.readouterr().err


def test_check_engine_error_for_unstructured_stdout(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = '{"type":"thread.started","thread_id":"abc"}\n'
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert vs[0].auto_fixable is False
    assert "review engine exited 1:" in vs[0].message
    assert stdout.strip() in vs[0].message


def test_check_engine_error_empty_stdout(tmp_path: Path) -> None:
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, "")):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert vs[0].message == "review failed without output"


@pytest.mark.parametrize(
    ("diff_lines", "unchecked", "expected_tier"),
    [
        (4, 0, "light"),
        (5, 0, "heavy"),
        (0, 1, "heavy"),
        (DIFF_LINES_FALLBACK, 0, "heavy"),
    ],
)
def test_check_pr_diff_tier_uses_diff_boundary_and_ac_state(
    tmp_path: Path,
    diff_lines: int,
    unchecked: int,
    expected_tier: str,
) -> None:
    """Refs sumipan/nexus#7400 AC1 — pr_diff step tier selects review engine tier."""
    tier_cfg = TierConfig(kind="pr_diff", light_max_diff_lines=5)
    gate = ReviewGate(
        _review_cfg(),
        "review",
        tmp_path,
        {},
        tier_cfg=tier_cfg,
        base_branch="main",
    )
    body = (
        "## Acceptance Criteria\n\n- [ ] item\n"
        if unchecked
        else ""
    )
    fallback = "diff_failed" if diff_lines == DIFF_LINES_FALLBACK else None
    with patch(
        "issuesmith.gates.review._worktree_diff_lines",
        return_value=(diff_lines, fallback),
    ):
        with patch(
            "issuesmith.engine.run_guarded_output",
            return_value=(0, "PIPELINE_STATUS: CP2_PASS\n"),
        ) as mock_run:
            assert gate.check(body, []) == []
    assert mock_run.call_args.kwargs["tier"] == expected_tier


@pytest.mark.parametrize(
    ("numstat", "expected"),
    [
        ("1\t2\tsrc/a.py\n-\t-\tasset.bin\n", (3, None)),
        ("1\t2\n", (DIFF_LINES_FALLBACK, "invalid_numstat")),
        ("-1\t2\tsrc/a.py\n", (DIFF_LINES_FALLBACK, "invalid_numstat")),
        ("x\t2\tsrc/a.py\n", (DIFF_LINES_FALLBACK, "invalid_numstat")),
    ],
)
def test_worktree_diff_lines_rejects_malformed_numstat(
    tmp_path: Path,
    numstat: str,
    expected: tuple[int, str | None],
) -> None:
    proc = MagicMock(returncode=0, stdout=numstat)
    with patch("issuesmith.gates.review.subprocess.run", return_value=proc):
        assert _worktree_diff_lines(tmp_path, "main") == expected


@pytest.mark.parametrize(
    "tier_cfg",
    [None, TierConfig(kind="design_doc")],
)
@pytest.mark.parametrize("review_tier", [None, "light"])
def test_check_without_pr_diff_tier_preserves_review_tier(
    tmp_path: Path,
    tier_cfg: TierConfig | None,
    review_tier: str | None,
) -> None:
    """Refs sumipan/nexus#7400 AC2 — fixed review.tier when step tier is not pr_diff."""
    review = ReviewConfig(
        role="review",
        template="cp2.md",
        success_status="CP2_PASS",
        failure_status="CP2_FAIL",
        tier=review_tier,
    )
    gate = ReviewGate(review, "x", tmp_path, {}, tier_cfg=tier_cfg)
    with patch(
        "issuesmith.engine.run_guarded_output",
        return_value=(0, "PIPELINE_STATUS: CP2_PASS\n"),
    ) as mock_run:
        assert gate.check("", []) == []
    assert mock_run.call_args.kwargs["tier"] == review_tier


def test_review_gate_doc_states_tier_priority() -> None:
    """Refs sumipan/nexus#7400 AC6 — review gate doc states pr_diff and tier priority."""
    import issuesmith.gates.review as review_mod

    doc = review_mod.__doc__ or ""
    assert "pr_diff" in doc
    assert "review.tier" in doc


def test_check_fail_maps_reply_block_problems_to_review_violations(
    tmp_path: Path,
) -> None:
    """Refs #8166 — failed reply block problems become review.<step_id> violations."""
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = (
        "```ghdag\n"
        "status: failed\n"
        "code: CP2\n"
        "problems:\n"
        "  - scope leak\n"
        "  - missing test\n"
        "```\n"
    )
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert [v.message for v in vs] == ["scope leak", "missing test"]
    assert all(v.rule_id == "review.cp2" for v in vs)


def test_check_invalid_reply_block_returns_engine_error_with_code(
    tmp_path: Path,
) -> None:
    """Refs #8166 — unparseable reply block yields review.<step_id>.engine_error."""
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = (
        "```ghdag\n"
        "status: failed\n"
        "problems: not-a-list\n"
        "```\n"
    )
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert "REPLY_BLOCK_INVALID" in vs[0].message


def test_check_failed_reply_without_problems_is_reply_invalid(
    tmp_path: Path,
) -> None:
    """Refs #8200 AC2 — failed block without problems is reply_invalid, not heading bullets."""
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = (
        "Problems:\n"
        "- legacy finding\n"
        "```ghdag\n"
        "status: failed\n"
        "code: CP2\n"
        "```\n"
    )
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.reply_invalid"
    assert "legacy finding" not in vs[0].message


def test_check_heading_bullets_without_reply_block_is_engine_error(
    tmp_path: Path,
) -> None:
    """Refs #8200 AC3 — Problems heading without reply block is engine_error."""
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = "Problems:\n- bad thing\nPIPELINE_STATUS: CP2_FAIL\n"
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"


def test_check_reply_block_problems_ignore_legacy_heading_bullets(
    tmp_path: Path,
) -> None:
    """Refs #8166 — structured block problems supersede Problems: bullets."""
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = (
        "Problems:\n"
        "- ignore me\n"
        "```ghdag\n"
        "status: failed\n"
        "code: CP2\n"
        "problems:\n"
        "  - structured only\n"
        "```\n"
    )
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert [v.message for v in vs] == ["structured only"]


def test_check_ok_reply_block_with_problems_returns_reply_block_invalid(
    tmp_path: Path,
) -> None:
    """Refs #8166 — status: ok with problems yields engine_error with ghdag hint."""
    gate = ReviewGate(_review_cfg(), "cp2", tmp_path, {})
    stdout = (
        "```ghdag\n"
        "status: ok\n"
        "code: CP2_PASS\n"
        "problems:\n"
        "  - x\n"
        "```\n"
    )
    with patch("issuesmith.engine.run_guarded_output", return_value=(1, stdout)):
        vs = gate.check("", [])
    assert len(vs) == 1
    assert vs[0].rule_id == "review.cp2.engine_error"
    assert "REPLY_BLOCK_INVALID" in vs[0].message
    assert "status: failed" in vs[0].message


def test_bundled_review_prompt_fail_example_has_problems() -> None:
    """Refs #8200 AC5 — bundled review.md fail example is a reply block with problems."""
    from ghdag.core import reply_block

    import issuesmith.gates.review as review_mod

    review_md = (
        Path(review_mod.__file__).resolve().parents[1]
        / "lines/develop/files/workflows/develop/prompts/review.md"
    )
    text = review_md.read_text(encoding="utf-8")
    assert "Problems:" not in text
    fail_idx = text.find("status: failed")
    assert fail_idx != -1
    fence = text.rfind("```ghdag", 0, fail_idx)
    block = reply_block.parse_reply_block(text[fence:])
    assert block is not None
    assert block.status == "failed"
    assert len(block.problems) >= 1
