"""tests for unified issuesmith CLI, packaging, and aliases (#2827 / #2821)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"

_MINIMAL_PHASES = [
    {"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"},
]


def _subprocess_config_path(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "issuesmith-cli.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/cli", "phases": _MINIMAL_PHASES}),
        encoding="utf-8",
    )
    return cfg_path


def _pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH", "")
    return str(SRC_PATH) + (f":{existing}" if existing else "")


def _run_module(
    *args: str,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
    warn_default: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": _pythonpath()}
    if warn_default:
        env["PYTHONWARNINGS"] = "default::DeprecationWarning"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "issuesmith", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=check,
        env=env,
    )


def test_engine_show_runs(tmp_path: Path) -> None:
    cfg_env = {"ISSUESMITH_CONFIG": str(_subprocess_config_path(tmp_path))}
    via_cli = _run_module("engine", "show", extra_env=cfg_env)
    assert via_cli.returncode == 0, via_cli.stderr
    assert via_cli.stdout.strip()


@pytest.mark.parametrize(
    ("argv", "code"),
    [
        (["nosuch"], "UNKNOWN_COMMAND"),
        (["andon", "list", "--bogus"], "UNKNOWN_ARGUMENT"),
        (["config", "show", "--bogus"], "UNKNOWN_ARGUMENT"),
        (["andon", "answer"], "MISSING_ARGUMENT"),
        (["queue", "status", "--bogus"], "UNKNOWN_ARGUMENT"),
        (["engine", "show", "--bogus"], "UNKNOWN_ARGUMENT"),
        (["metrics", "rework", "--bogus"], "UNKNOWN_ARGUMENT"),
        (["smoke", "--bogus"], "UNKNOWN_ARGUMENT"),
        (["publish"], "MISSING_ARGUMENT"),
        (["version-bump"], "MISSING_ARGUMENT"),
        (["queue", "nosuch"], "UNKNOWN_COMMAND"),
    ],
)
def test_usage_errors_exit_2_with_code_and_hint(capsys, argv: list[str], code: str) -> None:
    """#6779: usage errors use the shared CLI error contract."""
    from issuesmith import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if ln.strip()]
    assert lines[0].startswith(f"error[{code}]:")
    assert any(ln.startswith("hint:") for ln in lines)
    assert "issuesmith.queue" not in err
    assert "python3 -m issuesmith" not in err


def test_queue_runtime_errors_have_code(tmp_path: Path, capsys) -> None:
    """refs sumipan/nexus#7157: queue runtime failures use error codes."""
    from issuesmith import cli

    qp = tmp_path / "queue.jsonl"
    sp = tmp_path / "state.json"
    lp = tmp_path / "lock"
    paths = [
        "--queue-path",
        str(qp),
        "--state-path",
        str(sp),
        "--lock-path",
        str(lp),
    ]

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["queue", *paths, "release", "--issue", "999999"])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[NOT_FOUND]:")

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["queue", *paths, "dequeue", "--request-id", "missing", "--reason", "x"])
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[NOT_FOUND]:")


def test_resume_in_handlers() -> None:
    from issuesmith.cli import _HANDLERS
    assert "resume" in _HANDLERS


def test_resume_in_usage() -> None:
    from issuesmith.cli import _USAGE
    lines = _USAGE.splitlines()
    standalone = [ln for ln in lines if "resume" in ln and "milestone" not in ln]
    assert standalone, "resume not listed as standalone command in _USAGE"


def test_recover_unknown_subcommand_exits_nonzero(tmp_path: Path) -> None:
    cfg_env = {"ISSUESMITH_CONFIG": str(_subprocess_config_path(tmp_path))}
    proc = _run_module("recover", "1", extra_env=cfg_env, check=False)
    assert proc.returncode != 0


def test_recover_not_in_handlers() -> None:
    from issuesmith.cli import _HANDLERS

    assert "recover" not in _HANDLERS


def test_dispatch_sets_caller_step_env_for_python_steps(monkeypatch) -> None:
    import os

    from issuesmith.cli import _cmd_dispatch
    from issuesmith.worktree import CALLER_STEP_ENV_VAR

    seen: list[str | None] = []

    def fake_dispatch(argv: list[str]) -> int:
        seen.append(os.environ.get(CALLER_STEP_ENV_VAR))
        return 0

    # The publish step runs this suite with the caller step already exported.
    monkeypatch.delenv(CALLER_STEP_ENV_VAR, raising=False)
    monkeypatch.setattr("issuesmith.ops.dispatch.main", fake_dispatch)
    assert _cmd_dispatch(["p0", "issue_number=1"]) == 0
    assert seen == ["p0"]
    assert os.environ.get(CALLER_STEP_ENV_VAR) is None


def test_gate_design_lint_issue_form_routes_to_entry(monkeypatch) -> None:
    calls: list[list[str]] = []
    import issuesmith.cli as cli_mod
    import issuesmith.design_lint_entry as entry

    monkeypatch.setattr(entry, "main", lambda: calls.append(list(sys.argv)))
    assert cli_mod._cmd_gate(["design_lint", "99"]) == 0
    assert calls and calls[0][-1] == "99"


