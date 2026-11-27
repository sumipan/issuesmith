"""Public exception classes keep IssuesmithError contract — sumipan/issuesmith#8128."""

from __future__ import annotations

import pytest

from issuesmith.ac_contract import GateMaterializationError, GrepError
from issuesmith.bump_commits import BumpCommitsError
from issuesmith.config import ConfigError
from issuesmith.contract import LabelWriteForbidden, RetrySignal
from issuesmith.diagnose import ProposalError
from issuesmith.errors import IssuesmithError
from issuesmith.gates import GateBuildError
from issuesmith.gates.command import CommandStartError
from issuesmith.merge import MergeStateInfo, MergeStateTimeoutError
from issuesmith.observe.main_health import MainHealthError
from issuesmith.pr_scope import DerivedAllowPathsError
from issuesmith.queue_store import QueueValidationError
from issuesmith.verbs.acceptance import AcceptanceError
from issuesmith.verbs.children import ChildPlanError
from issuesmith.worktree_python import WorktreePythonError

_PUBLIC_ERRORS: list[tuple[type[BaseException], type[BaseException], str, BaseException]] = [
    (ConfigError, ValueError, "CONFIG_ERROR", ConfigError("config", hint="fix yaml")),
    (QueueValidationError, ValueError, "INVALID_VALUE", QueueValidationError("queue", hint="fix")),
    (DerivedAllowPathsError, ValueError, "INVALID_VALUE", DerivedAllowPathsError("paths", hint="fix")),
    (ProposalError, ValueError, "INVALID_VALUE", ProposalError("proposal", hint="fix")),
    (AcceptanceError, RuntimeError, "INVALID_VALUE", AcceptanceError("accept", hint="fix")),
    (ChildPlanError, RuntimeError, "INVALID_VALUE", ChildPlanError(["violation"], hint="fix")),
    (GateBuildError, ValueError, "CONFIG_ERROR", GateBuildError("gate", hint="fix")),
    (
        LabelWriteForbidden,
        RuntimeError,
        "CONFLICT",
        LabelWriteForbidden("p1", ["lbl"], hint="remove label writes"),
    ),
    (CommandStartError, RuntimeError, "FAILED", CommandStartError("cmd", hint="fix command")),
    (
        MergeStateTimeoutError,
        Exception,
        "FAILED",
        MergeStateTimeoutError(
            1,
            MergeStateInfo("BLOCKED", "CONFLICTING", "OPEN", "feat/x"),
            hint="wait for merge",
        ),
    ),
    (BumpCommitsError, RuntimeError, "FAILED", BumpCommitsError("git", hint="fix git")),
    (
        GateMaterializationError,
        RuntimeError,
        "FAILED",
        GateMaterializationError("materialize", hint="fix fetch"),
    ),
    (GrepError, RuntimeError, "FAILED", GrepError("grep", hint="fix repo")),
    (WorktreePythonError, RuntimeError, "FAILED", WorktreePythonError("python", hint="fix bin")),
    (MainHealthError, RuntimeError, "FAILED", MainHealthError("health", hint="fix observe")),
]


@pytest.mark.parametrize(
    ("cls", "stdlib_base", "code", "sample"),
    _PUBLIC_ERRORS,
    ids=[row[0].__name__ for row in _PUBLIC_ERRORS],
)
def test_public_error_classes_preserve_contract(
    cls: type[BaseException],
    stdlib_base: type[BaseException],
    code: str,
    sample: BaseException,
) -> None:
    assert issubclass(cls, IssuesmithError)
    assert issubclass(cls, stdlib_base)
    assert not issubclass(RetrySignal, IssuesmithError)
    exc = sample
    assert isinstance(exc, cls)
    assert exc.code == code  # type: ignore[attr-defined]
    assert exc.hint is not None  # type: ignore[attr-defined]
