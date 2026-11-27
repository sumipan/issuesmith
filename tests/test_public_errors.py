"""Public exception classes keep IssuesmithError contract — sumipan/issuesmith#8128."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from issuesmith.ac_contract import GateMaterializationError, GrepError, materialize_gate_root
from issuesmith.bump_commits import BumpCommitsError, _run_git
from issuesmith.config import ConfigError, load_config, reset_config_cache
from issuesmith.contract import LabelWriteForbidden, RetrySignal
from issuesmith.diagnose import ProposalError, parse_proposal
from issuesmith.errors import IssuesmithError
from issuesmith.forge_guard import ReadOnlyLabelForge
from issuesmith.gates import GATE_REGISTRY, GateBuildContext, GateBuildError
from issuesmith.gates.command import CommandStartError, run_command
from issuesmith.merge import MergeStateInfo, MergeStateTimeoutError, wait_merge_state
from issuesmith.observe.main_health import MainHealthError, _ensure_worktree
from issuesmith.pr_scope import DerivedAllowPathsError, derived_allow_paths_from_result
from issuesmith.queue_store import QueueBlock, QueueStore, QueueValidationError
from issuesmith.verbs.acceptance import AcceptanceError, check_acceptance
from issuesmith.verbs.children import ChildPlanError, plan_children
from issuesmith.worktree_python import WorktreePythonError, python_bin_for

REPO_ROOT = Path(__file__).resolve().parents[1]

_STDILIB_BASE: dict[str, type[BaseException]] = {
    "ConfigError": ValueError,
    "QueueValidationError": ValueError,
    "DerivedAllowPathsError": ValueError,
    "ProposalError": ValueError,
    "AcceptanceError": RuntimeError,
    "ChildPlanError": RuntimeError,
    "GateBuildError": ValueError,
    "CommandStartError": RuntimeError,
    "LabelWriteForbidden": RuntimeError,
    "BumpCommitsError": RuntimeError,
    "GateMaterializationError": RuntimeError,
    "GrepError": RuntimeError,
    "WorktreePythonError": RuntimeError,
    "MainHealthError": RuntimeError,
    "MergeStateTimeoutError": Exception,
}

_ERROR_CODES = {
    "ConfigError": "CONFIG_ERROR",
    "QueueValidationError": "INVALID_VALUE",
    "DerivedAllowPathsError": "INVALID_VALUE",
    "ProposalError": "INVALID_VALUE",
    "AcceptanceError": "INVALID_VALUE",
    "ChildPlanError": "INVALID_VALUE",
    "GateBuildError": "CONFIG_ERROR",
    "CommandStartError": "FAILED",
    "LabelWriteForbidden": "CONFLICT",
    "BumpCommitsError": "FAILED",
    "GateMaterializationError": "FAILED",
    "GrepError": "FAILED",
    "WorktreePythonError": "FAILED",
    "MainHealthError": "FAILED",
    "MergeStateTimeoutError": "FAILED",
}


@pytest.mark.parametrize(
    "name",
    list(_ERROR_CODES),
)
def test_public_error_classes_preserve_contract(name: str) -> None:
    cls = {
        "ConfigError": ConfigError,
        "QueueValidationError": QueueValidationError,
        "DerivedAllowPathsError": DerivedAllowPathsError,
        "ProposalError": ProposalError,
        "AcceptanceError": AcceptanceError,
        "ChildPlanError": ChildPlanError,
        "GateBuildError": GateBuildError,
        "CommandStartError": CommandStartError,
        "LabelWriteForbidden": LabelWriteForbidden,
        "BumpCommitsError": BumpCommitsError,
        "GateMaterializationError": GateMaterializationError,
        "GrepError": GrepError,
        "WorktreePythonError": WorktreePythonError,
        "MainHealthError": MainHealthError,
        "MergeStateTimeoutError": MergeStateTimeoutError,
    }[name]
    assert issubclass(cls, IssuesmithError)
    assert issubclass(cls, _STDILIB_BASE[name])
    assert cls.code == _ERROR_CODES[name]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "error",
    [
        ConfigError("bad config"),
        QueueValidationError("bad queue value"),
        DerivedAllowPathsError("bad derived path"),
        ProposalError("bad proposal"),
        AcceptanceError("bad acceptance contract"),
        GateBuildError("bad gate config"),
        CommandStartError("bad command"),
        BumpCommitsError("bad bump state"),
        GateMaterializationError("bad gate worktree"),
        GrepError("bad grep"),
        WorktreePythonError("bad python command"),
        MainHealthError("bad main health state"),
    ],
)
def test_public_errors_supply_default_hint(error: IssuesmithError) -> None:
    assert error.hint


def test_retry_signal_not_issuesmith_error() -> None:
    assert not issubclass(RetrySignal, IssuesmithError)


def test_config_error_hint_via_load_config(tmp_path, monkeypatch) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text("repo: x/y\nconcurrency: {default: nope}\n", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError) as exc:
        load_config(cfg_path)
    assert exc.value.hint


def test_queue_validation_error_hint(tmp_path) -> None:
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "s.json",
        lock_path=tmp_path / "l",
    )
    with pytest.raises(QueueValidationError) as exc:
        store.add_block(QueueBlock("bogus", "k", "", None, "test"))
    assert exc.value.hint


def test_derived_allow_paths_error_hint(tmp_path) -> None:
    path = tmp_path / "p1.md"
    path.write_text("derived_allow_paths:\n  - /etc/passwd\n", encoding="utf-8")
    with pytest.raises(DerivedAllowPathsError) as exc:
        derived_allow_paths_from_result(path)
    assert exc.value.hint


def test_proposal_error_hint() -> None:
    with pytest.raises(ProposalError) as exc:
        parse_proposal(
            "no fence",
            andon_record={},
            issue_body="",
            config=MagicMock(),
        )
    assert exc.value.hint


def test_acceptance_error_hint() -> None:
    from unittest.mock import patch

    with patch(
        "issuesmith.verbs.acceptance.materialize_gate_root",
        side_effect=GateMaterializationError(
            "fetch failed",
            hint="git fetch -q origin main in /path/to/repo",
        ),
    ):
        with pytest.raises(AcceptanceError) as exc:
            check_acceptance("", [], repos={"x/y": REPO_ROOT}, base_branch="main")
    assert exc.value.hint


def test_child_plan_error_hint() -> None:
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\ntarget_repo: sumipan/issuesmith\n```\n",
        "labels": [],
        "milestone": {"number": 1},
    }
    with pytest.raises(ChildPlanError) as exc:
        plan_children(client, 1, repo="sumipan/issuesmith")
    assert exc.value.hint


def test_gate_build_error_hint() -> None:
    import issuesmith.gate_rules  # noqa: F401

    entry = GATE_REGISTRY["lint"]
    ctx = GateBuildContext(
        worktree_path=None,
        allow_paths=[],
        base_branch="main",
    )
    with pytest.raises(GateBuildError) as exc:
        entry.build(ctx)
    assert exc.value.hint


def test_label_write_forbidden_hint() -> None:
    forge = ReadOnlyLabelForge(MagicMock(), step_id="p1", mode="enforce")
    with pytest.raises(LabelWriteForbidden) as exc:
        forge.add_label(1, "ns:running")
    assert exc.value.hint


def test_bump_commits_error_hint(tmp_path) -> None:
    with pytest.raises(BumpCommitsError) as exc:
        _run_git(tmp_path, "status")
    assert exc.value.hint


def test_gate_materialization_error_hint(tmp_path, monkeypatch) -> None:
    import subprocess
    import tempfile

    monkeypatch.setattr(
        tempfile,
        "mkdtemp",
        lambda *, prefix, dir=None: str(tmp_path / "gate-root"),
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    with pytest.raises(GateMaterializationError) as exc:
        materialize_gate_root(repo, "main", "gate-")
    assert exc.value.hint


def test_grep_error_hint() -> None:
    from issuesmith.ac_contract import grep_matches

    with pytest.raises(GrepError) as exc:
        grep_matches(
            REPO_ROOT,
            {"pattern": "[", "paths": ["."]},
            ref="not-a-valid-ref",
        )
    assert exc.value.hint


def test_worktree_python_error_hint(tmp_path, monkeypatch) -> None:
    import yaml

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/app",
                "tests": {"python_bin_command": ["/no/such/python-bin-cmd", "x"]},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(WorktreePythonError) as exc:
        python_bin_for(tmp_path)
    assert exc.value.hint


def test_main_health_error_hint(tmp_path) -> None:
    from issuesmith.config import MainHealthConfig

    cfg = MainHealthConfig(
        worktree=tmp_path / "missing",
        command=("true",),
        base_branch="main",
        timeout_seconds=30,
        halt_on_red=True,
    )
    with pytest.raises(MainHealthError) as exc:
        _ensure_worktree(cfg, repo_root=None, run=MagicMock())
    assert exc.value.hint


def test_merge_state_timeout_error_hint(monkeypatch) -> None:
    client = MagicMock()

    def blocked(*_args, **_kwargs):
        return MergeStateInfo("BLOCKED", "UNKNOWN", "OPEN", "feat/x")

    monkeypatch.setattr("issuesmith.merge.get_merge_state", blocked)
    with pytest.raises(MergeStateTimeoutError) as exc:
        wait_merge_state(
            client,
            "owner/repo",
            1,
            timeout_seconds=0,
            poll_interval_seconds=0,
            blocked_backoff_seconds=(),
            sleep=lambda _s: None,
        )
    assert exc.value.hint


def test_command_start_error_hint(tmp_path) -> None:
    from issuesmith.gates.command import CommandStartError

    with pytest.raises(CommandStartError) as exc:
        run_command(tmp_path, ["/no/such/binary-issuesmith-test"], python_root=tmp_path)
    assert exc.value.hint
