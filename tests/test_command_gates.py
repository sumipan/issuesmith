"""Command-driven lint/tests gates (refs sumipan/issuesmith#7788)."""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates.command import CommandLintGate, CommandStartError
from issuesmith.gates.worktree import WORKTREE_GATES


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=False,
    )


def _setup_repo(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    clone = tmp_path / "clone"
    origin.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(origin)], check=True, capture_output=True)
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "T")
    (origin / "README.md").write_text("init\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-m", "init")
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "T")
    return origin, clone


def _install_script(tmp_path: Path, name: str, body: str) -> Path:
    bindir = tmp_path / "tools"
    bindir.mkdir(exist_ok=True)
    path = bindir / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return bindir


def _load_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: dict,
    *,
    path_prefix: Path | None = None,
) -> None:
    base = {
        "repo": "example/repo",
        "phases": [
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "prepare",
                "handler": "develop",
            }
        ],
    }
    base.update(extra)
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(base), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    if path_prefix is not None:
        monkeypatch.setenv(
            "PATH",
            f"{path_prefix}{os.pathsep}{os.environ.get('PATH', '')}",
        )
    reset_config_cache()


def _decoy_executables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "decoys"
    bindir.mkdir(exist_ok=True)
    for name in ("ruff", "python", "python3"):
        marker = bindir / f"{name}_touched"
        script = bindir / name
        script.write_text(
            f"#!/bin/sh\n: >{marker}\nexit 0\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return bindir


@pytest.fixture()
def command_repo(tmp_path: Path) -> tuple[Path, Path]:
    return _setup_repo(tmp_path)


def test_lint_command_runs_instead_of_ruff(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7788 AC1 — lint.command replaces ruff."""
    _origin, clone = command_repo
    decoys = _decoy_executables(tmp_path, monkeypatch)
    tools = _install_script(
        tmp_path,
        "lint.sh",
        """\
        #!/bin/sh
        if [ "$1" = "fail" ]; then
          echo lint failed
          exit 1
        fi
        exit 0
        """,
    )
    _load_config(
        tmp_path,
        monkeypatch,
        {"lint": {"command": [str(tools / "lint.sh"), "fail"]}},
        path_prefix=tools,
    )
    gate = WORKTREE_GATES["lint"](clone, [], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "lint.command_failure"
    assert "lint failed" in violations[0].message
    assert not (decoys / "ruff_touched").exists()


def test_tests_command_runs_instead_of_pytest(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7788 AC2 — tests.command replaces pytest."""
    _origin, clone = command_repo
    decoys = _decoy_executables(tmp_path, monkeypatch)
    tools = _install_script(
        tmp_path,
        "test.sh",
        """\
        #!/bin/sh
        echo tests failed
        exit 1
        """,
    )
    _load_config(
        tmp_path,
        monkeypatch,
        {"tests": {"command": [str(tools / "test.sh")], "flaky_reruns": 0}},
        path_prefix=tools,
    )
    gate = WORKTREE_GATES["tests"](clone, [], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "tests.command_failure"
    assert not (decoys / "python_touched").exists()
    assert not (decoys / "python3_touched").exists()


def test_junit_report_ignores_failures_preexisting_on_base(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7788 AC3 — JUnit baseline filters preexisting failures."""
    origin, clone = command_repo
    tools = _install_script(
        tmp_path,
        "junit.sh",
        """\
        #!/bin/sh
        mkdir -p reports
        if [ -f .emit_t2 ]; then
          cat > reports/junit.xml <<'EOF'
        <?xml version="1.0"?>
        <testsuites><testsuite>
        <testcase classname="S" name="T1"><failure/></testcase>
        <testcase classname="S" name="T2"><failure/></testcase>
        </testsuite></testsuites>
        EOF
        else
          cat > reports/junit.xml <<'EOF'
        <?xml version="1.0"?>
        <testsuites><testsuite>
        <testcase classname="S" name="T1"><failure/></testcase>
        </testsuite></testsuites>
        EOF
        fi
        exit 1
        """,
    )
    _load_config(
        tmp_path,
        monkeypatch,
        {
            "tests": {
                "command": [str(tools / "junit.sh")],
                "report": "junit",
                "report_path": "reports/junit.xml",
                "flaky_reruns": 0,
            }
        },
        path_prefix=tools,
    )
    _git(origin, "checkout", "main")
    _git(clone, "fetch", "origin")
    _git(clone, "merge", "origin/main")
    (clone / ".emit_t2").write_text("1\n", encoding="utf-8")
    _git(clone, "add", ".emit_t2")
    _git(clone, "commit", "-m", "enable t2")

    gate = WORKTREE_GATES["tests"](clone, [], "main")
    violations = gate.check("", [])
    failures = [v for v in violations if v.rule_id == "tests.junit_failure"]
    assert len(failures) == 1
    assert failures[0].location == "S::T2"


def test_junit_report_marks_rerun_pass_as_flaky(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7788 AC4 — flaky tests warn without blocking failures."""
    _origin, clone = command_repo
    tools = _install_script(
        tmp_path,
        "flaky.sh",
        """\
        #!/bin/sh
        mkdir -p reports
        if [ ! -f .flaky_head ]; then
          exit 0
        fi
        n=0
        if [ -f .flaky_run ]; then
          n=$(cat .flaky_run)
        fi
        echo $((n + 1)) > .flaky_run
        if [ "$n" -eq 0 ]; then
          cat > reports/junit.xml <<'EOF'
        <?xml version="1.0"?>
        <testsuites><testsuite>
        <testcase classname="S" name="T2"><failure/></testcase>
        </testsuite></testsuites>
        EOF
          exit 1
        fi
        exit 0
        """,
    )
    (clone / ".flaky_head").write_text("1\n", encoding="utf-8")
    _git(clone, "add", ".flaky_head")
    _git(clone, "commit", "-m", "flaky head marker")
    _load_config(
        tmp_path,
        monkeypatch,
        {
            "tests": {
                "command": [str(tools / "flaky.sh")],
                "report": "junit",
                "report_path": "reports/junit.xml",
                "flaky_reruns": 1,
            }
        },
        path_prefix=tools,
    )
    gate = WORKTREE_GATES["tests"](clone, [], "main")
    violations = gate.check("", [])
    flaky = [v for v in violations if v.rule_id == "tests.flaky"]
    assert len(flaky) == 1
    assert flaky[0].severity == "warn"
    assert not [v for v in violations if v.severity == "fail"]


def test_lint_fix_command_runs_once_then_check_passes(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7788 AC5 — lint fix_command auto-fixes once."""
    _origin, clone = command_repo
    tools = _install_script(
        tmp_path,
        "lint.sh",
        """\
        #!/bin/sh
        if [ ! -f .fixed ]; then
          echo broken
          exit 1
        fi
        exit 0
        """,
    )
    _install_script(
        tmp_path,
        "lint-fix.sh",
        """\
        #!/bin/sh
        touch .fixed
        exit 0
        """,
    )
    _load_config(
        tmp_path,
        monkeypatch,
        {
            "lint": {
                "command": [str(tools / "lint.sh")],
                "fix_command": [str(tools / "lint-fix.sh")],
            }
        },
        path_prefix=tools,
    )
    from issuesmith.gates.base import ContractInput

    gate = WORKTREE_GATES["lint"](clone, [], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "lint.command_failure"
    assert violations[0].auto_fixable is True
    gate.fix(ContractInput(body=""))
    assert gate.check("", []) == []


def test_missing_executable_raises_command_start_error(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7788 AC6 — missing command raises CommandStartError."""
    _origin, clone = command_repo
    missing = tmp_path / "no-such-lint-tool"
    _load_config(
        tmp_path,
        monkeypatch,
        {"lint": {"command": [str(missing)]}},
    )
    gate = WORKTREE_GATES["lint"](clone, [], "main")
    with pytest.raises(CommandStartError):
        gate.check("", [])

    _load_config(
        tmp_path,
        monkeypatch,
        {"tests": {"command": [str(missing)], "flaky_reruns": 0}},
    )
    gate2 = WORKTREE_GATES["tests"](clone, [], "main")
    with pytest.raises(CommandStartError):
        gate2.check("", [])


def test_lint_command_success_returns_empty(
    command_repo: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _origin, clone = command_repo
    tools = _install_script(tmp_path, "ok.sh", "#!/bin/sh\nexit 0\n")
    _load_config(
        tmp_path,
        monkeypatch,
        {"lint": {"command": [str(tools / "ok.sh")]}},
        path_prefix=tools,
    )
    gate = CommandLintGate(clone, [str(tools / "ok.sh")])
    assert gate.check("", []) == []
