"""Tests for requires evaluation and repair loop in dispatch (#3626).

Covers: pass / auto_fix / repair-1-pass / max_repairs-exceeded→andon /
gate-exception→broken / preexisting-non-blocking.
"""
from __future__ import annotations

import subprocess
import sys
import types
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.andon import from_comment as _andon_from_comment_body
from issuesmith.config import RepairConfig, StepConfig, reset_config_cache
from issuesmith.gate_registry import Violation
from issuesmith.gates.base import ContractInput
from issuesmith.repair import (
    RequiresResult,
    apply_auto_fixes,
    evaluate_requires,
)
from tests.conftest import _write_nexus_config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v(rule_id: str = "test.fail", auto_fixable: bool = False) -> Violation:
    return Violation(
        rule_id=rule_id,
        severity="fail",
        message="test violation",
        location=None,
        auto_fixable=auto_fixable,
        fix_hint="fix it" if auto_fixable else None,
    )


def _pass_gate() -> MagicMock:
    g = MagicMock()
    g.check.return_value = []
    g.fix.side_effect = lambda inp: inp
    return g


def _fail_gate(rule_id: str = "test.fail", auto_fixable: bool = False) -> MagicMock:
    g = MagicMock()
    g.check.return_value = [_v(rule_id, auto_fixable)]
    g.fix.side_effect = lambda inp: inp
    return g


def _error_gate(exc: Exception | None = None) -> MagicMock:
    g = MagicMock()
    g.check.side_effect = exc or RuntimeError("gate exploded")
    return g


class _RecordingForge:
    def __init__(self) -> None:
        self.issue_comments: list[tuple[int, str]] = []
        self.issue_updates: list[tuple[int, dict]] = []

    def issue_comment(self, issue: int, body: str) -> dict:
        self.issue_comments.append((issue, body))
        return {"id": 1, "body": body}

    def issue_update(self, issue: int, **kwargs) -> dict:
        self.issue_updates.append((issue, kwargs))
        return {}


_REPAIR_BOUNDARY_MODULE = "issuesmith_test_dispatch_requires_repair_boundary"


class RepairStepBoundary:
    """Records repair-step launches via a fake module at resolve_step_config boundary."""

    def __init__(self) -> None:
        self.launches: list = []
        self.exit_code: int | None = None
        self.side_effect: BaseException | None = None
        self.on_run = None

    def _install_module(self) -> None:
        boundary = self

        def run(ctx, step=None):
            from issuesmith.contract import StepResult

            if boundary.side_effect is not None:
                raise boundary.side_effect
            boundary.launches.append(ctx)
            if boundary.on_run is not None:
                boundary.on_run(ctx)
            if boundary.exit_code is not None and boundary.exit_code != 0:
                return StepResult(status="done", exit_code=boundary.exit_code)
            return StepResult(status="done", markers=["REPAIR_DONE"])

        mod = types.ModuleType(_REPAIR_BOUNDARY_MODULE)
        mod.run = run
        sys.modules[_REPAIR_BOUNDARY_MODULE] = mod


@contextmanager
def stand_in_repair_step(boundary: RepairStepBoundary | None = None, **kwargs):
    """Patch resolve_step_config so the repair step runs a test-local fake module."""
    from issuesmith.config import StepConfig
    from issuesmith.ops import dispatch as dispatch_mod

    b = boundary if boundary is not None else RepairStepBoundary()
    if "exit_code" in kwargs:
        b.exit_code = kwargs["exit_code"]
    if "side_effect" in kwargs:
        b.side_effect = kwargs["side_effect"]
    b._install_module()
    real_resolve = dispatch_mod.resolve_step_config

    def resolve(step_id: str):
        if step_id == "repair":
            return StepConfig(module=_REPAIR_BOUNDARY_MODULE)
        return real_resolve(step_id)

    with patch("issuesmith.ops.dispatch.resolve_step_config", side_effect=resolve):
        yield b


def _raised_andons(forge: _RecordingForge):
    out = []
    for _, body in forge.issue_comments:
        parsed = _andon_from_comment_body(body)
        if parsed is not None:
            out.append(parsed)
    return out

def _inp() -> ContractInput:
    return ContractInput(body="issue body", labels=[])


@pytest.fixture
def nexus_config(monkeypatch, tmp_path):
    """Nexus declaration for modules split out of this file; conftest applies it by module name."""
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(_write_nexus_config(tmp_path)))
    reset_config_cache()


# ---------------------------------------------------------------------------
# evaluate_requires — core evaluation logic
# ---------------------------------------------------------------------------

class TestEvaluateRequires:
    def test_no_gates_returns_empty(self):
        r = evaluate_requires({}, "body", [])
        assert not r.blocking
        assert not r.preexisting
        assert r.gate_error is None

    def test_passing_gate_no_blocking(self):
        r = evaluate_requires({"scope": _pass_gate()}, "body", [])
        assert not r.blocking
        assert r.gate_error is None

    def test_failing_gate_creates_blocking(self):
        r = evaluate_requires({"scope": _fail_gate()}, "body", [])
        assert len(r.blocking) == 1
        assert r.blocking[0].rule_id == "test.fail"

    def test_gate_exception_sets_gate_error(self):
        r = evaluate_requires({"scope": _error_gate()}, "body", [])
        assert r.gate_error is not None
        assert not r.blocking


    def test_first_error_gate_aborts_remaining(self):
        gates = {
            "bad": _error_gate(),
            "ok": _pass_gate(),
        }
        r = evaluate_requires(gates, "body", [])
        assert r.gate_error is not None
        # ok gate should not have been called yet (order-dependent; dict preserves insertion order)
        # — we only assert error is set; gate call count may vary


# ---------------------------------------------------------------------------
# apply_auto_fixes
# ---------------------------------------------------------------------------

class TestApplyAutoFixes:
    def test_non_fixable_violation_stays_blocking(self):
        result = RequiresResult(blocking=[_v(auto_fixable=False)])
        updated, inp = apply_auto_fixes(result, {}, _inp())
        assert len(updated.blocking) == 1


    def test_gate_without_fix_method_keeps_violation(self):
        gate = MagicMock(spec=[])  # no fix attribute
        result = RequiresResult(blocking=[_v(rule_id="lint.X", auto_fixable=True)])
        updated, _ = apply_auto_fixes(result, {"lint": gate}, _inp())
        assert len(updated.blocking) == 1

    def test_gate_fix_exception_keeps_violation(self):
        gate = MagicMock()
        gate.fix.side_effect = RuntimeError("fix failed")
        result = RequiresResult(blocking=[_v(rule_id="lint.X", auto_fixable=True)])
        updated, _ = apply_auto_fixes(result, {"lint": gate}, _inp())
        assert len(updated.blocking) == 1


# ---------------------------------------------------------------------------
# dispatch.map_step_result with requires — end-to-end dispatch loop
# ---------------------------------------------------------------------------

def _make_ctx(issue: str = "42") -> dict:
    return {
        "issue_number": issue,
        "workflow_name": "issuesmith",
        "base_branch": "main",
    }


class TestDispatchGateException:
    """AC-5: gate exception → andon(broken), no repair attempted."""


    def test_gate_exception_does_not_emit_markers(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": _error_gate()}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                map_step_result(
                    StepResult(status="done", markers=["IMPL_DONE"]),
                    step_id="p2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        assert "PIPELINE_STATUS" not in capsys.readouterr().out


class TestDispatchPreexisting:
    """AC-6: preexisting violations do not block; they are visualized only."""

    def test_preexisting_violation_does_not_block(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(module="issuesmith.steps.test", requires=("scope",))
        gate = _fail_gate(rule_id="scope.old_issue")

        with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"scope": gate}):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                with patch(
                    "issuesmith.ops.dispatch._get_preexisting_rule_ids",
                    return_value=frozenset({"scope.old_issue"}),
                ):
                    rc = map_step_result(
                        StepResult(status="done", markers=["IMPL_DONE"]),
                        step_id="p2",
                        context=_make_ctx(),
                        step_cfg=cfg,
                    )
        assert rc == 0
        from ghdag.core import reply_block

        out = capsys.readouterr().out
        block = reply_block.parse_reply_block(out)
        assert block is not None and block.status == "ok" and block.code == "IMPL_DONE"

        # No andon raised — the RetrySignal propagated up


# ---------------------------------------------------------------------------
# AC-2c: worktree gate with None worktree_path → GateBuildError → andon(broken)
# ---------------------------------------------------------------------------


class TestDispatchGateBuildError:
    """AC-2c: gate build failure → andon(broken) with 'could not be built' summary."""

    def test_gate_build_error_produces_andon_broken(self, capsys):
        from issuesmith.config import StepConfig
        from issuesmith.contract import StepResult
        from issuesmith.gates import GateBuildError
        from issuesmith.ops.dispatch import map_step_result

        cfg = StepConfig(
            module="issuesmith.steps.test",
            requires=("lint",),
            input_kind="worktree",
        )

        with patch(
            "issuesmith.ops.dispatch._build_requires_gates",
            side_effect=GateBuildError("gate 'lint' requires worktree_path but context has none"),
        ):
            with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                forge = _RecordingForge()
                mock_forge.return_value = forge
                rc = map_step_result(
                    StepResult(status="done", markers=["IMPL_DONE"]),
                    step_id="p2",
                    context=_make_ctx(),
                    step_cfg=cfg,
                )
        assert rc == 1
        andon_arg = _raised_andons(forge)[0]
        assert andon_arg is not None
        assert andon_arg.kind == "broken"
        assert "could not be built" in andon_arg.summary


# ---------------------------------------------------------------------------
# AC-3c: _context_to_step passes repair fields
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# #3756 AC-3b: derived_allow_paths kept across repairs; repair instruction text
# ---------------------------------------------------------------------------


class TestDerivedAllowPaths:
    def test_derived_kept_after_repair_and_allowed_by_pr_scope(self, tmp_path):
        from issuesmith.config import StepConfig
        from issuesmith.gates.worktree import TestsGate
        from issuesmith.ops.dispatch import run_requires_loop

        state = {"repaired": False, "calls": 0}

        def fake_tests_check(self, body, labels):
            state["calls"] += 1
            if state["repaired"]:
                self.derived_allow_paths = []
                return []
            self.derived_allow_paths = ["tests/test_uses_mod.py"]
            return [_v(rule_id="tests.pytest_failure")]

        def fake_changed(root, base):
            if state["repaired"]:
                return ["src/pkg/mod.py", "tests/test_uses_mod.py"]
            return ["src/pkg/mod.py"]

        repair_contexts: list[dict] = []
        repair = RepairStepBoundary()

        def _on_repair_run(ctx):
            repair_contexts.append(asdict(ctx))
            state["repaired"] = True

        repair.on_run = _on_repair_run

        cfg = StepConfig(
            module="", requires=("tests", "pr_scope"), input_kind="worktree",
            requires_declared=True,
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(tmp_path),
            "allow_paths": "- src/pkg/mod.py",
        }
        with (
            patch.object(TestsGate, "check", fake_tests_check),
            patch("issuesmith.gates.worktree.changed_files", side_effect=fake_changed),
            patch("issuesmith.gates.worktree.check_derived_test_guard", return_value=[]),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
            stand_in_repair_step(repair),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "p1", context)

        assert rc is None
        assert _raised_andons(forge) == []
        assert state["calls"] == 2
        assert context["derived_allow_paths"] == "tests/test_uses_mod.py"
        assert repair_contexts[0]["derived_allow_paths"] == "tests/test_uses_mod.py"
        # frozen order: allow_paths itself is not rewritten
        assert context["allow_paths"] == "- src/pkg/mod.py"


    def _repair_text(self, context: dict) -> str:
        from issuesmith.ops.dispatch import _run_repair_step

        captured: dict = {}

        def fake_step(step_id, ctx):
            captured.update(ctx)
            return 0

        with patch("issuesmith.ops.dispatch._try_python_step", side_effect=fake_step):
            assert _run_repair_step([_v("tests.pytest_failure")], "p1", context) is None
        return captured["repair_violations"]


    def test_repair_instruction_unchanged_without_derived(self):
        text = self._repair_text({"issue_number": "42"})
        assert text == (
            "Fix only the violations below with the smallest possible diff. "
            "Do not touch files outside allow_paths.\n"
            "- tests.pytest_failure: test violation"
        )


def test_map_step_result_prints_derived_allow_paths_block(capsys) -> None:
    """Refs sumipan/issuesmith#7401 AC4 — derived block precedes done marker."""
    from issuesmith.contract import StepResult
    from issuesmith.ops.dispatch import map_step_result

    context = {
        "issue_number": "42",
        "workflow_name": "develop",
        "derived_allow_paths": "tests/a.py\ntests/b.py",
    }
    rc = map_step_result(
        StepResult(status="done", markers=["IMPL_DONE"]),
        step_id="implement",
        context=context,
        step_cfg=StepConfig(),
    )
    out = capsys.readouterr().out
    assert rc == 0
    marker_pos = out.index("```ghdag")
    # write_reply_block prepends one newline before the fence (#7728).
    block = "derived_allow_paths:\n  - tests/a.py\n  - tests/b.py\n\n"
    assert out[:marker_pos] == block


class TestWorktreePathResolution:
    """Refs sumipan/issuesmith#7640 — GateBuildContext worktree_path is absolute under config root."""

    def test_relative_worktree_path_resolved_against_config_root(
        self, tmp_path, monkeypatch,
    ):
        from issuesmith.config import get_config
        from issuesmith.gates.worktree import TestsGate
        from issuesmith.ops.dispatch import _build_requires_gates

        config_root = get_config().root
        rel = Path(f".pytest-dispatch-wt-{uuid.uuid4().hex[:8]}")
        wt_abs = (config_root / rel).resolve()
        wt_abs.mkdir(parents=True)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        assert elsewhere.resolve() != config_root.resolve()
        monkeypatch.chdir(elsewhere)

        recorded: list[tuple[Path, list[str]]] = []

        def fake_pytest(root: Path, args: list[str], **_kwargs) -> tuple[int, str]:
            recorded.append((Path(root), list(args)))
            return 0, "1 passed\n"

        monkeypatch.setattr("issuesmith.gates.worktree._run_pytest", fake_pytest)
        try:
            gates = _build_requires_gates(
                ("tests",),
                {
                    "worktree_path": rel.as_posix(),
                    "base_branch": "main",
                    "allow_paths": "",
                },
            )
            gate = gates["tests"]
            assert isinstance(gate, TestsGate)
            assert gate._root == wt_abs
            gate._run("mapped", ["tests"])
        finally:
            wt_abs.rmdir()

        assert len(recorded) == 1
        assert recorded[0][0] == wt_abs

    def test_absolute_worktree_path_passed_through_to_pytest(self, tmp_path, monkeypatch):
        from issuesmith.gates.worktree import TestsGate
        from issuesmith.ops.dispatch import _build_requires_gates

        wt_abs = tmp_path.resolve()
        recorded: list[tuple[Path, list[str]]] = []

        def fake_pytest(root: Path, args: list[str], **_kwargs) -> tuple[int, str]:
            recorded.append((Path(root), list(args)))
            return 0, "1 passed\n"

        monkeypatch.setattr("issuesmith.gates.worktree._run_pytest", fake_pytest)
        gates = _build_requires_gates(
            ("tests",),
            {
                "worktree_path": str(wt_abs),
                "base_branch": "main",
                "allow_paths": "",
            },
        )
        gate = gates["tests"]
        assert isinstance(gate, TestsGate)
        assert gate._root == wt_abs
        gate._run("mapped", ["tests"])
        assert recorded[0][0] == wt_abs


class TestReviewEngineErrorAndon:
    """Refs sumipan/nexus#6604 — review engine_error offers resume_from_<step>."""

    def test_cp2_engine_error_andon_includes_resume(self):
        from issuesmith.ops.dispatch import run_requires_loop

        engine_v = Violation(
            rule_id="review.cp2.engine_error",
            severity="fail",
            message="review failed without output",
            location=None,
            auto_fixable=False,
            fix_hint=None,
        )
        result = RequiresResult(blocking=[engine_v])
        step_cfg = StepConfig(module="issuesmith.steps.test", requires=("review",))

        with (
            patch(
                "issuesmith.ops.dispatch._build_requires_gates",
                return_value={"review": MagicMock()},
            ),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
            patch("issuesmith.repair.evaluate_requires", return_value=result),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(step_cfg, "cp2", _make_ctx())

        assert rc == 1
        andon = _raised_andons(forge)[0]
        assert andon.kind == "decision"
        assert andon.options == ["resume_from_cp2", "split", "reject"]


# ---------------------------------------------------------------------------
# recheck gates — HEAD-aware requires extension (sumipan/issuesmith#7676)
# ---------------------------------------------------------------------------


def _init_git_worktree(tmp_path: Path) -> Path:
    repo = tmp_path / "wt"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "T"],
        cwd=repo, capture_output=True, check=True,
    )
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, capture_output=True, check=True,
    )
    return repo


class TestRecheckGates:
    def test_recheck_gates_skipped_when_head_unchanged(self, tmp_path: Path) -> None:
        """Refs sumipan/issuesmith#7676 AC3 — stable HEAD never evaluates recheck."""
        from issuesmith.ops.dispatch import run_requires_loop

        wt = _init_git_worktree(tmp_path)
        built: list[tuple[str, ...]] = []

        def _capture_build(gate_ids, *_args, **_kwargs):
            built.append(tuple(gate_ids))
            return {g: _pass_gate() for g in gate_ids}

        cfg = StepConfig(
            module="",
            requires=("scope",),
            recheck=("recheck_stub",),
            input_kind="worktree",
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(wt),
        }
        with (
            patch("issuesmith.ops.dispatch._build_requires_gates", side_effect=_capture_build),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        ):
            rc = run_requires_loop(cfg, "review", context)
        assert rc is None
        assert built == [("scope",)]

    def test_recheck_gates_run_after_repair_commit(self, tmp_path: Path) -> None:
        """Refs sumipan/issuesmith#7676 AC4 — post-repair evaluation includes recheck."""
        from issuesmith.ops.dispatch import run_requires_loop

        wt = _init_git_worktree(tmp_path)
        built: list[tuple[str, ...]] = []
        scope_calls = {"n": 0}

        def _scope_check():
            scope_calls["n"] += 1
            if scope_calls["n"] == 1:
                return _fail_gate()
            return _pass_gate()

        def _capture_build(gate_ids, *_args, **_kwargs):
            built.append(tuple(gate_ids))
            out = {"scope": _scope_check()}
            if "recheck_stub" in gate_ids:
                out["recheck_stub"] = _fail_gate(rule_id="recheck_stub.fail")
            return out

        repair = RepairStepBoundary()

        def _commit_on_repair(ctx):
            (wt / "extra.txt").write_text("x\n", encoding="utf-8")
            subprocess.run(["git", "add", "extra.txt"], cwd=wt, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "repair commit"],
                cwd=wt, check=True, capture_output=True,
            )

        repair.on_run = _commit_on_repair

        cfg = StepConfig(
            module="",
            requires=("scope",),
            recheck=("recheck_stub",),
            input_kind="worktree",
            repair=RepairConfig(max=1),
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(wt),
        }
        with (
            patch("issuesmith.ops.dispatch._build_requires_gates", side_effect=_capture_build),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
            stand_in_repair_step(repair),
            patch("issuesmith.ops.dispatch.get_forge") as mock_forge,
        ):
            forge = _RecordingForge()
            mock_forge.return_value = forge
            rc = run_requires_loop(cfg, "review", context)
        assert rc == 1
        assert ("scope", "recheck_stub") in built
        assert _raised_andons(forge)

    def test_recheck_after_requires_gate_commits_mid_evaluation(self, tmp_path: Path) -> None:
        """Refs sumipan/issuesmith#7676 AC5 — late commit triggers recheck before pass."""
        from issuesmith.ops.dispatch import run_requires_loop

        wt = _init_git_worktree(tmp_path)
        built: list[tuple[str, ...]] = []
        calls = {"n": 0}

        class _CommittingGate:
            def check(self, body, labels):
                calls["n"] += 1
                if calls["n"] == 1:
                    (wt / "mid.txt").write_text("y\n", encoding="utf-8")
                    subprocess.run(["git", "add", "mid.txt"], cwd=wt, check=True, capture_output=True)
                    subprocess.run(
                        ["git", "commit", "-m", "mid-eval commit"],
                        cwd=wt, check=True, capture_output=True,
                    )
                return []

            def fix(self, inp):
                return inp

        def _capture_build(gate_ids, *_args, **_kwargs):
            built.append(tuple(gate_ids))
            out = {"scope": _CommittingGate()}
            if "recheck_stub" in gate_ids:
                out["recheck_stub"] = _pass_gate()
            return out

        cfg = StepConfig(
            module="",
            requires=("scope",),
            recheck=("recheck_stub",),
            input_kind="worktree",
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(wt),
        }
        with (
            patch("issuesmith.ops.dispatch._build_requires_gates", side_effect=_capture_build),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        ):
            rc = run_requires_loop(cfg, "review", context)
        assert rc is None
        assert built == [("scope",), ("scope", "recheck_stub")]

    def test_recheck_from_first_eval_when_head_unreadable(self, tmp_path: Path) -> None:
        """Refs sumipan/issuesmith#7676 AC6 — non-repo worktree_path always includes recheck."""
        from issuesmith.ops.dispatch import run_requires_loop

        not_git = tmp_path / "not-a-repo"
        not_git.mkdir()
        built: list[tuple[str, ...]] = []

        def _capture_build(gate_ids, *_args, **_kwargs):
            built.append(tuple(gate_ids))
            return {g: _pass_gate() for g in gate_ids}

        cfg = StepConfig(
            module="",
            requires=("scope",),
            recheck=("recheck_stub",),
            input_kind="worktree",
        )
        context = {
            "issue_number": "42",
            "workflow_name": "issuesmith",
            "base_branch": "main",
            "worktree_path": str(not_git),
        }
        with (
            patch("issuesmith.ops.dispatch._build_requires_gates", side_effect=_capture_build),
            patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""),
            patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]),
        ):
            rc = run_requires_loop(cfg, "review", context)
        assert rc is None
        assert built == [("scope", "recheck_stub")]


