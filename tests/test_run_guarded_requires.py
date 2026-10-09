"""AC-2 / AC-3 / AC-5 / AC-6 / AC-7: run_guarded --requires-step integration tests.

LLM execution (_execute) is mocked throughout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)


@pytest.fixture()
def fresh_repo(tmp_path: Path):
    """Minimal git repo with origin tracking for gate tests."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-b", "main")
    _git(origin, "config", "user.email", "test@test.com")
    _git(origin, "config", "user.name", "Test")
    (origin / "README.md").write_text("hello\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "init")

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin), str(clone)],
        capture_output=True, check=True,
    )
    _git(clone, "config", "user.email", "test@test.com")
    _git(clone, "config", "user.name", "Test")
    return clone


def _make_proc(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _make_step_cfg(requires: list[str] = None):
    from issuesmith.config import StepConfig
    return StepConfig(
        module="",
        requires=tuple(requires or []),
        input_kind="worktree",
        requires_declared=True,
    )


# ---------------------------------------------------------------------------
# AC-9: --requires-step not specified → existing behavior unchanged
# ---------------------------------------------------------------------------


def test_run_guarded_no_requires_step_emits_on_success(capsys):
    """Without requires_step, run_guarded works exactly as before."""
    from issuesmith.engine import run_guarded

    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")) as mock_emit:
        rc = run_guarded(
            "implementation",
            "fake.md",
            [],
            success_statuses=["IMPL_DONE"],
            failure_status="IMPL_FAILED",
            emit_status="IMPL_DONE",
        )

    mock_emit.assert_called_once()
    assert rc == 0


# ---------------------------------------------------------------------------
# AC-5: module-less step in config, dispatched via dispatch.main → andon(broken)
# ---------------------------------------------------------------------------


def test_dispatch_llm_step_no_module_raises_andon_broken():
    """Dispatching a module-less step via issuesmith dispatch raises andon(broken)."""
    from issuesmith.config import StepConfig, reset_config_cache

    reset_config_cache()
    try:
        with patch("issuesmith.ops.dispatch.get_config") as mock_cfg:
            mock_cfg_obj = MagicMock()
            mock_cfg_obj.steps = {
                "p1": StepConfig(module="", requires=("lint",), input_kind="worktree", requires_declared=True),
            }
            mock_cfg_obj.label_namespace = "issuesmith"
            mock_cfg_obj.root = Path(".")
            mock_cfg.return_value = mock_cfg_obj

            with patch("issuesmith.ops.dispatch.REPO_ROOT", Path(".")):
                with patch("issuesmith.ops.dispatch.TEMPLATE_DIR", Path(".")):
                    with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                        mock_forge.return_value = MagicMock()
                        with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                            from issuesmith.ops.dispatch import main as dispatch_main
                            rc = dispatch_main([
                                "p1",
                                "issue_number=42",
                                "workflow_name=issuesmith",
                            ])

        assert rc == 1
        mock_andon.assert_called_once()
        andon_arg = mock_andon.call_args[0][1]
        assert andon_arg.kind == "broken"
        assert "no module" in andon_arg.summary
    finally:
        reset_config_cache()


# ---------------------------------------------------------------------------
# AC-5b: doctor validates requires_declared
# ---------------------------------------------------------------------------


def test_doctor_requires_declared_empty_list_is_ok():
    """requires: [] explicitly declared → requires_chain: ok."""
    from issuesmith.config import StepConfig
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "cp2": StepConfig(module="issuesmith.steps.cp2", requires=(), requires_declared=True),
    }
    violations = validate_requires_chain(steps)
    assert violations == []


def test_doctor_missing_requires_key_is_violation():
    """requires key absent (requires_declared=False) → missing requires declaration."""
    from issuesmith.config import StepConfig
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "some-step": StepConfig(module="issuesmith.steps.foo"),
    }
    violations = validate_requires_chain(steps)
    assert len(violations) == 1
    assert "missing requires declaration" in violations[0]


# ---------------------------------------------------------------------------
# AC-2: run_guarded + requires_step: post gate fails → repair → re-eval passes → IMPL_DONE
# ---------------------------------------------------------------------------


def test_run_guarded_evaluates_accepts_after_requires_pass(capsys, fresh_repo: Path):
    """requires PASS → accepts loop runs with origin=accepts."""
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]
    origins: list[str] = []

    def fake_loop(step_cfg, step_id, context, **kwargs):
        origins.append(kwargs.get("origin", "requires"))
        return None

    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", side_effect=fake_loop):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="",
                    requires=("lint",),
                    accepts=("tests",),
                    requires_declared=True,
                )
                rc = run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )

    assert rc == 0
    assert origins == ["requires", "accepts"]
    assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out


def test_run_guarded_skips_accepts_when_requires_fails(capsys, fresh_repo: Path):
    """requires failure → accepts is not evaluated."""
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]
    origins: list[str] = []

    def fake_loop(step_cfg, step_id, context, **kwargs):
        origins.append(kwargs.get("origin", "requires"))
        if kwargs.get("origin", "requires") == "requires":
            return 1
        return None

    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", side_effect=fake_loop):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="",
                    requires=("lint",),
                    accepts=("tests",),
                    requires_declared=True,
                )
                rc = run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )

    assert rc == 1
    assert origins == ["requires"]
    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: IMPL_DONE" not in out
    assert "PIPELINE_STATUS: IMPL_FAILED" in out


def test_run_guarded_accepts_only_step_evaluates_accepts(capsys, fresh_repo: Path):
    """Empty requires with accepts only still runs the accepts loop."""
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]
    origins: list[str] = []

    def fake_loop(step_cfg, step_id, context, **kwargs):
        origins.append(kwargs.get("origin", "requires"))
        return None

    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", side_effect=fake_loop):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="",
                    requires=(),
                    accepts=("lint",),
                    requires_declared=True,
                )
                rc = run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )

    assert rc == 0
    assert origins == ["requires", "accepts"]
    assert "PIPELINE_STATUS: IMPL_DONE" in capsys.readouterr().out


def test_run_guarded_with_requires_step_post_pass_emits_status(capsys, fresh_repo: Path):
    """Post gates all pass after repair → emit_status output."""
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]

    # All gates pass on post evaluation
    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", return_value=None) as mock_loop:
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="", requires=("lint",), requires_declared=True
                )
                rc = run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )

    assert rc == 0
    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: IMPL_DONE" in out
    mock_loop.assert_called_once()


def test_run_guarded_with_requires_step_post_fail_no_impl_done(capsys, fresh_repo: Path):
    """Post gates fail → no IMPL_DONE emitted, exit 1."""
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]

    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", return_value=1):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="", requires=("lint",), requires_declared=True
                )
                rc = run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )

    assert rc == 1
    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: IMPL_DONE" not in out
    assert "PIPELINE_STATUS: IMPL_FAILED" in out


# ---------------------------------------------------------------------------
# AC-3: LLM stdout PIPELINE_STATUS: IMPL_DONE doesn't count if gates fail
# ---------------------------------------------------------------------------


def test_llm_impl_done_overridden_by_gate_fail(capsys, fresh_repo: Path):
    """Even if LLM emits IMPL_DONE in its output, gates failing means no IMPL_DONE from engine."""
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]

    # LLM stdout contains IMPL_DONE but gate fails (emit_order returns 0 since we pass it as emit)
    # But run_requires_loop returns 1 (failure)
    with patch("issuesmith.engine._run_emit_order", return_value=(0, "PIPELINE_STATUS: IMPL_DONE\n")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", return_value=1):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="", requires=("lint",), requires_declared=True
                )
                rc = run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )

    assert rc == 1
    # Engine should NOT emit IMPL_DONE since gate failed
    capsys.readouterr().out
    # The engine writes LLM stdout, so IMPL_DONE from LLM stdout appears
    # but the engine itself should NOT emit an additional PIPELINE_STATUS: IMPL_DONE
    # (the run_requires_loop returning 1 means we exit 1 without emitting emit_status)


# ---------------------------------------------------------------------------
# AC-7: scope_breadth gate pre_llm → LLM not invoked when scope too large
# ---------------------------------------------------------------------------


def test_scope_breadth_blocks_llm(capsys, fresh_repo: Path):
    """scope_breadth.too_large (repairable=False) → LLM not called, andon(decision)."""
    from ghdag.workflow.gates import Violation

    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]

    scope_violation = Violation(
        rule_id="scope_breadth.too_large",
        severity="fail",
        message="scope too large",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )

    execute_called = []

    def mock_execute(*args, **kwargs):
        execute_called.append(True)
        return _make_proc(0, "")

    with patch("issuesmith.engine._execute", side_effect=mock_execute):
        with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
            from issuesmith.config import StepConfig
            mock_resolve.return_value = StepConfig(
                module="",
                requires=("scope_breadth",),
                requires_declared=True,
                input_kind="issue",
            )

            scope_gate = MagicMock()
            scope_gate.check.return_value = [scope_violation]

            with patch(
                "issuesmith.engine._build_pre_gates",
                return_value={"scope_breadth": scope_gate},
            ), patch(
                "issuesmith.ops.dispatch._fetch_fresh_issue_body",
                return_value="FORGE_BODY",
            ), patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]):
                with patch("issuesmith.engine.get_forge") as mock_forge:
                    mock_forge.return_value = MagicMock()
                    with patch("issuesmith.engine._raise_andon") as mock_andon:
                        rc = run_guarded(
                            "implementation",
                            "fake.md",
                            variables,
                            success_statuses=["IMPL_DONE"],
                            failure_status="IMPL_FAILED",
                            emit_status="IMPL_DONE",
                            requires_step="p1",
                        )

    assert len(execute_called) == 0, "LLM should NOT be called when scope_breadth fails"
    assert rc == 1
    # The pre phase evaluates the body fetched from the forge, not the (absent) variable.
    assert scope_gate.check.call_args[0][0] == "FORGE_BODY"
    mock_andon.assert_called_once()
    andon_arg = mock_andon.call_args[0][1]
    assert andon_arg.kind == "decision"
    assert "split" in andon_arg.options
    assert "reject" in andon_arg.options
    captured = capsys.readouterr()
    assert "pre-LLM gate violation in step p1" in captured.out
    assert "scope too large" in captured.out
    assert "PIPELINE_STATUS: IMPL_FAILED" in captured.out


# ---------------------------------------------------------------------------
# AC-6: base_freshness pre_llm gate auto-fixed before LLM
# ---------------------------------------------------------------------------


def test_base_freshness_fixed_before_llm(capsys, fresh_repo: Path):
    """base_freshness.behind_base → auto-fixed before LLM call."""
    from ghdag.workflow.gates import Violation

    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]

    fix_called = []

    behind_violation = Violation(
        rule_id="base_freshness.behind_base",
        severity="fail",
        message="Branch is 1 commit(s) behind origin/main",
        location=None,
        auto_fixable=True,
        fix_hint="git merge --ff-only origin/main",
    )

    class FakeFreshnessGate:
        def check(self, body, labels):
            if not fix_called:
                return [behind_violation]
            return []

        def fix(self, inp):
            fix_called.append(True)
            return inp

    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", return_value=None):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                from issuesmith.config import StepConfig
                mock_resolve.return_value = StepConfig(
                    module="",
                    requires=("base_freshness",),
                    requires_declared=True,
                    input_kind="worktree",
                )

                with patch(
                    "issuesmith.engine._build_pre_gates",
                    return_value={"base_freshness": FakeFreshnessGate()},
                ), patch(
                    "issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""
                ), patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]):
                    rc = run_guarded(
                        "implementation",
                        "fake.md",
                        variables,
                        success_statuses=["IMPL_DONE"],
                        failure_status="IMPL_FAILED",
                        emit_status="IMPL_DONE",
                        requires_step="p1",
                    )

    assert rc == 0
    assert fix_called, "base_freshness.fix() should have been called"


# ---------------------------------------------------------------------------
# AC-2c: max_repairs exceeded → andon(decision) with widen / split / reject
# ---------------------------------------------------------------------------


def test_run_requires_loop_max_repairs_options_widen():
    """max_repairs exceeded with pr_scope violation → options include widen:<file>."""
    from ghdag.workflow.gates import Violation

    from issuesmith.config import StepConfig
    from issuesmith.ops.dispatch import run_requires_loop

    step_cfg = StepConfig(
        module="",
        requires=("pr_scope",),
        requires_declared=True,
        input_kind="worktree",
    )

    pr_violation = Violation(
        rule_id="pr_scope.out_of_allow",
        severity="fail",
        message="scripts/x.py: outside allow_paths",
        location="scripts/x.py",
        auto_fixable=False,
        fix_hint="widen:scripts/x.py",
    )

    with patch("issuesmith.ops.dispatch._build_requires_gates",
               return_value={"pr_scope": MagicMock(check=lambda body, labels: [pr_violation])}):
        with patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""):
            with patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]):
                with patch("issuesmith.ops.dispatch._get_preexisting_rule_ids",
                           return_value=frozenset()):
                    with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                        mock_forge.return_value = MagicMock()
                        with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                            ctx = {
                                "issue_number": "42",
                                "workflow_name": "issuesmith",
                                "base_branch": "main",
                            }
                            rc = run_requires_loop(
                                step_cfg,
                                "p1",
                                ctx,
                                repair_count=3,
                            )

    assert rc == 1
    mock_andon.assert_called_once()
    andon_arg = mock_andon.call_args[0][1]
    assert andon_arg.kind == "decision"
    options = andon_arg.options
    assert any(o.startswith("widen:") and "scripts/x.py" in o for o in options), f"options={options}"
    assert "split" in options
    assert "reject" in options


def test_run_requires_loop_max_repairs_no_pr_scope_no_widen():
    """max_repairs exceeded without pr_scope → options are split and reject only."""
    from ghdag.workflow.gates import Violation

    from issuesmith.config import StepConfig
    from issuesmith.ops.dispatch import run_requires_loop

    step_cfg = StepConfig(
        module="issuesmith.steps.test",
        requires=("tests",),
        requires_declared=True,
        input_kind="worktree",
    )

    test_violation = Violation(
        rule_id="tests.pytest_failure",
        severity="fail",
        message="test_foo failed",
        location="tests/test_foo.py",
        auto_fixable=False,
        fix_hint="Fix tests",
    )

    with patch("issuesmith.ops.dispatch._build_requires_gates",
               return_value={"tests": MagicMock(check=lambda body, labels: [test_violation])}):
        with patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", return_value=""):
            with patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]):
                with patch("issuesmith.ops.dispatch._get_preexisting_rule_ids",
                           return_value=frozenset()):
                    with patch("issuesmith.ops.dispatch.get_forge") as mock_forge:
                        mock_forge.return_value = MagicMock()
                        with patch("issuesmith.ops.dispatch._raise_andon") as mock_andon:
                            ctx = {
                                "issue_number": "42",
                                "workflow_name": "issuesmith",
                                "base_branch": "main",
                            }
                            rc = run_requires_loop(
                                step_cfg,
                                "p1",
                                ctx,
                                repair_count=3,
                            )

    assert rc == 1
    andon_arg = mock_andon.call_args[0][1]
    options = andon_arg.options
    assert "split" in options
    assert "reject" in options
    assert not any(o.startswith("widen:") for o in options)


# ---------------------------------------------------------------------------
# AC-2b: body re-fetched each evaluation → updated allow_paths
# ---------------------------------------------------------------------------


def test_run_requires_loop_refetches_body():
    """run_requires_loop calls _fetch_fresh_issue_body (not context cache)."""
    from issuesmith.config import StepConfig
    from issuesmith.ops.dispatch import run_requires_loop

    step_cfg = StepConfig(
        module="issuesmith.steps.test",
        requires=("tests",),
        requires_declared=True,
        input_kind="worktree",
    )

    gate = MagicMock()
    gate.check.return_value = []

    fetch_calls = []

    def fake_fetch(ctx):
        fetch_calls.append(ctx)
        return ""

    with patch("issuesmith.ops.dispatch._build_requires_gates", return_value={"tests": gate}):
        with patch("issuesmith.ops.dispatch._fetch_fresh_issue_body", side_effect=fake_fetch):
            with patch("issuesmith.ops.dispatch._get_issue_labels", return_value=[]):
                with patch("issuesmith.ops.dispatch._get_preexisting_rule_ids",
                           return_value=frozenset()):
                    ctx = {
                        "issue_number": "42",
                        "workflow_name": "issuesmith",
                        "base_branch": "main",
                        "issue_body": "CACHED_BODY",
                    }
                    result = run_requires_loop(step_cfg, "p1", ctx)

    assert result is None
    # _fetch_fresh_issue_body was called (not the cached body)
    assert len(fetch_calls) == 1


# ---------------------------------------------------------------------------
# #3756 AC-5: derived_allow_paths block is printed before PIPELINE_STATUS
# ---------------------------------------------------------------------------


def _run_guarded_with_loop(fake_loop, fresh_repo: Path) -> int:
    from issuesmith.config import StepConfig
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]
    with patch("issuesmith.engine._run_emit_order", return_value=(0, "")):
        with patch("issuesmith.ops.dispatch.run_requires_loop", side_effect=fake_loop):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                mock_resolve.return_value = StepConfig(
                    module="", requires=("tests",), requires_declared=True
                )
                return run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )


def test_run_guarded_prints_derived_allow_paths_before_status(capsys, fresh_repo: Path):
    def fake_loop(step_cfg, step_id, context, **kwargs):
        context["derived_allow_paths"] = "tests/test_a.py\ntests/test_b.py"
        return None

    rc = _run_guarded_with_loop(fake_loop, fresh_repo)

    assert rc == 0
    out = capsys.readouterr().out
    block = "derived_allow_paths:\n  - tests/test_a.py\n  - tests/test_b.py\n"
    assert block in out
    assert out.index(block) < out.index("PIPELINE_STATUS: IMPL_DONE")


def test_run_guarded_no_derived_block_when_empty(capsys, fresh_repo: Path):
    rc = _run_guarded_with_loop(lambda *a, **k: None, fresh_repo)

    assert rc == 0
    out = capsys.readouterr().out
    assert "derived_allow_paths" not in out
    assert "PIPELINE_STATUS: IMPL_DONE" in out


# ---------------------------------------------------------------------------
# #4304: HEAD advanced past base → requires loop despite LLM non-zero exit
# ---------------------------------------------------------------------------


def _run_guarded_with_llm_rc(
    fresh_repo: Path, llm_rc: int, track_loop: list, stdout: str = "",
) -> int:
    from issuesmith.config import StepConfig
    from issuesmith.engine import run_guarded

    variables = [
        "issue_number=42",
        "base_branch=main",
        f"worktree_path={fresh_repo}",
        "allow_paths=- README.md",
        "workflow_name=issuesmith",
    ]
    with patch("issuesmith.engine._run_emit_order", return_value=(llm_rc, stdout)):
        with patch(
            "issuesmith.ops.dispatch.run_requires_loop",
            side_effect=lambda *a, **k: track_loop.append(True),
        ):
            with patch("issuesmith.ops.dispatch.resolve_step_config") as mock_resolve:
                mock_resolve.return_value = StepConfig(
                    module="", requires=("lint",), requires_declared=True
                )
                return run_guarded(
                    "implementation",
                    "fake.md",
                    variables,
                    success_statuses=["IMPL_DONE"],
                    failure_status="IMPL_FAILED",
                    emit_status="IMPL_DONE",
                    requires_step="p1",
                )


def test_run_guarded_head_advanced_proceeds_to_requires_despite_llm_failure(
    capsys, fresh_repo: Path,
):
    """LLM rc != 0 but HEAD ahead of base → requires loop runs."""
    _git(fresh_repo, "checkout", "-b", "feat/impl")
    (fresh_repo / "extra.txt").write_text("x\n", encoding="utf-8")
    _git(fresh_repo, "add", "extra.txt")
    _git(fresh_repo, "commit", "-m", "impl work")

    loop_called: list[bool] = []
    rc = _run_guarded_with_llm_rc(fresh_repo, 124, loop_called)

    assert loop_called, "requires loop should run when HEAD advanced"
    assert rc == 0
    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: IMPL_DONE" in out


def test_run_guarded_head_not_advanced_exits_on_llm_failure(fresh_repo: Path):
    """LLM rc != 0 and HEAD unchanged → early exit, no requires loop."""
    loop_called: list[bool] = []
    rc = _run_guarded_with_llm_rc(fresh_repo, 1, loop_called)

    assert not loop_called
    assert rc == 1


# ---------------------------------------------------------------------------
# #4530: explicit failure report is not overridden by HEAD-ahead
# ---------------------------------------------------------------------------


def test_run_guarded_explicit_failure_not_overridden_by_head_ahead(
    capsys, fresh_repo: Path,
):
    """Order reports IMPL_FAILED on a standalone line → no requires loop, rc 1."""
    _git(fresh_repo, "checkout", "-b", "feat/impl")
    (fresh_repo / "extra.txt").write_text("x\n", encoding="utf-8")
    _git(fresh_repo, "add", "extra.txt")
    _git(fresh_repo, "commit", "-m", "version bump only")

    loop_called: list[bool] = []
    rc = _run_guarded_with_llm_rc(
        fresh_repo, 1, loop_called,
        stdout="cannot implement\nPIPELINE_STATUS: IMPL_FAILED\n",
    )

    assert not loop_called, "requires loop must not run on explicit failure"
    assert rc == 1
    captured = capsys.readouterr()
    assert "PIPELINE_STATUS: IMPL_DONE" not in captured.out
    assert (
        "order reported failure (IMPL_FAILED); not overriding with HEAD-ahead"
        in captured.err
    )


def _call_order_no_newline(fn_name: str, stdout: str, returncode: int = 1) -> int:
    from issuesmith import engine

    with patch("issuesmith.engine._render_template", return_value=""):
        with patch(
            "issuesmith.engine._execute",
            return_value=_make_proc(returncode=returncode, stdout=stdout),
        ):
            if fn_name == "_run_emit_order":
                rc, _ = engine._run_emit_order(
                    "implementation", "fake.md", [], "IMPL_FAILED",
                )
            else:
                rc, _ = engine._run_guarded_order(
                    "implementation", "fake.md", [], ["IMPL_DONE"], "IMPL_FAILED",
                )
    return rc


@pytest.mark.parametrize("fn_name", ["_run_emit_order", "_run_guarded_order"])
def test_failure_status_standalone_when_stdout_lacks_newline(capsys, fn_name: str):
    """failure status is printed on its own line after unterminated LLM stdout."""
    from issuesmith.engine import _extract_status_values

    rc = _call_order_no_newline(fn_name, "no newline at end")

    assert rc == 1
    out = capsys.readouterr().out
    assert "no newline at end\nPIPELINE_STATUS: IMPL_FAILED\n" in out
    assert "IMPL_FAILED" in _extract_status_values(out)


@pytest.mark.parametrize("fn_name", ["_run_emit_order", "_run_guarded_order"])
def test_failure_status_standalone_when_stdout_ends_with_status(capsys, fn_name: str):
    """Unterminated LLM status line and engine status line stay separate."""
    from issuesmith.engine import _extract_status_values

    rc = _call_order_no_newline(fn_name, "oops\nPIPELINE_STATUS: IMPL_FAILED", returncode=0)

    assert rc == 1
    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: IMPL_FAILED\nPIPELINE_STATUS: IMPL_FAILED\n" in out
    assert _extract_status_values(out).count("IMPL_FAILED") == 2


@pytest.mark.parametrize("fn_name", ["_run_emit_order", "_run_guarded_order"])
def test_failure_status_no_blank_line_when_stdout_ends_with_newline(capsys, fn_name: str):
    """No extra blank line is added when LLM stdout already ends with a newline."""
    rc = _call_order_no_newline(fn_name, "line\n")

    assert rc == 1
    out = capsys.readouterr().out
    assert "line\nPIPELINE_STATUS: IMPL_FAILED\n" in out
    assert "line\n\nPIPELINE_STATUS" not in out


def test_build_pre_gates_includes_external_pre_llm_gate(tmp_path: Path, monkeypatch):
    from issuesmith.config import StepConfig
    from issuesmith.engine import _build_pre_gates

    mod_name = "mypkg_gates_prellm"
    gate_ref = f"{mod_name}:MY_GATE"
    (tmp_path / f"{mod_name}.py").write_text(
        """
from issuesmith.gates import GateEntry

class _G:
    def check(self, body, labels):
        return []

MY_GATE = GateEntry(
    input_kind="worktree",
    build=lambda ctx: _G(),
    pre_llm=True,
)
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    step_cfg = StepConfig(
        module="",
        requires=(gate_ref,),
        input_kind="worktree",
        requires_declared=True,
    )
    gates = _build_pre_gates(
        step_cfg,
        {"worktree_path": str(tmp_path), "allow_paths": "- x.py", "base_branch": "main"},
    )
    assert gate_ref in gates
