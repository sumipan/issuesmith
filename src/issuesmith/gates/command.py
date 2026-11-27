"""Configurable lint/tests gates driven by issuesmith.yaml commands (#7788)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Sequence

from issuesmith.errors import IssuesmithError
from issuesmith.gate_registry import Violation
from issuesmith.gates.base import ContractInput

_OUTPUT_TAIL = 2000
_PYTEST_TIMEOUT_ENV = "ISSUESMITH_PYTEST_TIMEOUT_SEC"
_PYTEST_TIMEOUT_DEFAULT_SEC = 1500.0
_INTERRUPTED_RC: frozenset[int] = frozenset({143, 137, -15, -9})


class CommandStartError(IssuesmithError, RuntimeError):
    """The configured command or python_bin launcher could not be started."""

    code = "FAILED"


def _func_id(test_id: str) -> str:
    return re.sub(r"\[.*\]$", "", test_id)


def run_command(
    root: Path,
    argv: Sequence[str],
    *,
    python_root: Path | None = None,
) -> tuple[int, str]:
    """Run ``argv`` with cwd=root; return (exit code, combined stdout+stderr)."""
    from issuesmith.config import get_config
    from issuesmith.worktree_python import WorktreePythonError, python_bin_for

    env = dict(os.environ)
    env.pop("ISSUESMITH_CONFIG", None)
    resolve_root = python_root if python_root is not None else root
    try:
        python_bin = python_bin_for(resolve_root)
    except WorktreePythonError as exc:
        cmd_hint = " ".join(get_config().tests.python_bin_command)
        raise CommandStartError(
            f"{cmd_hint or 'python_bin_command'}: {exc}"
        ) from exc
    if python_bin is not None:
        prefix = str(python_bin)
        env["PATH"] = prefix + os.pathsep + env.get("PATH", "")
    limit = _command_timeout_sec()
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            cwd=str(root),
            env=env,
            timeout=limit,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else (
            (exc.stdout or b"").decode(errors="replace")
        )
        tail = partial[-_OUTPUT_TAIL:]
        return 124, f"command timed out after {limit:.0f} s\n{tail}"
    except OSError as exc:
        raise CommandStartError(
            f"command could not be started: {argv!r}: {exc}",
            hint="check PATH, python_bin_command, and the command argv",
        ) from exc
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output


def read_junit_failed_ids(path: Path) -> list[str] | None:
    """Parse JUnit XML at ``path``; return failing test IDs or None when unreadable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    seen: set[str] = set()
    ids: list[str] = []
    for elem in root.iter("testcase"):
        if elem.find("failure") is None and elem.find("error") is None:
            continue
        name = (elem.get("name") or "").strip()
        if not name:
            continue
        classname = (elem.get("classname") or "").strip()
        test_id = f"{classname}::{name}" if classname else name
        if test_id not in seen:
            seen.add(test_id)
            ids.append(test_id)
    return ids


def _command_timeout_sec() -> float:
    raw = (os.environ.get(_PYTEST_TIMEOUT_ENV) or "").strip()
    try:
        return float(raw) if raw else _PYTEST_TIMEOUT_DEFAULT_SEC
    except ValueError:
        return _PYTEST_TIMEOUT_DEFAULT_SEC


def _interrupted_violation(rc: int) -> Violation:
    return Violation(
        rule_id="tests.interrupted",
        severity="fail",
        message=(
            f"pytest interrupted by external signal (rc={rc}, SIGTERM/SIGKILL);"
            " not a test failure"
        ),
        location=None,
        auto_fixable=False,
        fix_hint="Rerun only; do not modify code",
    )


def _tail(output: str) -> str:
    return output[-_OUTPUT_TAIL:]


def _is_tracked(root: Path, rel: str) -> bool:
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        capture_output=True,
        check=False,
        cwd=str(root),
    )
    return proc.returncode == 0


def _prepare_report(root: Path, rel: str) -> None:
    path = root / rel
    if path.is_file():
        path.unlink()


def _finalize_report(root: Path, rel: str) -> None:
    if rel and not _is_tracked(root, rel):
        (root / rel).unlink(missing_ok=True)


def _baseline_ref(root: Path, base_branch: str) -> str:
    """Return merge-base of HEAD and origin/<base>, or origin/<base> when unavailable."""
    remote = f"origin/{base_branch}"
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "merge-base", "HEAD", remote],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            if sha:
                return sha
    except Exception:
        pass
    return remote


def _baseline_junit_failed(
    root: Path,
    base_branch: str,
    command: Sequence[str],
    report_path: str,
) -> set[str] | None:
    """Return func-level IDs failing on the baseline ref, or None when unavailable."""
    tmp = tempfile.mkdtemp(dir=str(root.parent))
    baseline_ref = _baseline_ref(root, base_branch)
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", tmp, baseline_ref],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        wt = Path(tmp)
        _prepare_report(wt, report_path)
        try:
            rc, _ = run_command(wt, command, python_root=root)
        except CommandStartError:
            return None
        if rc in _INTERRUPTED_RC:
            return None
        report = wt / report_path
        ids = read_junit_failed_ids(report)
        if ids is None:
            return None
        return {_func_id(i) for i in ids}
    except Exception:
        return None
    finally:
        subprocess.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", tmp],
            capture_output=True,
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(root), "worktree", "prune"],
            capture_output=True,
            check=False,
        )
        shutil.rmtree(tmp, ignore_errors=True)


class CommandLintGate:
    """Run a configured lint command; optional fix_command for auto-fix."""

    def __init__(
        self,
        worktree_path: Path,
        command: Sequence[str],
        fix_command: Sequence[str] = (),
    ) -> None:
        self._root = worktree_path
        self._command = tuple(command)
        self._fix_command = tuple(fix_command)

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        rc, output = run_command(self._root, self._command)
        if rc == 0:
            return []
        if rc == 124:
            return [Violation(
                rule_id="lint.command_timeout",
                severity="fail",
                message=_tail(output),
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]
        auto_fixable = bool(self._fix_command)
        return [Violation(
            rule_id="lint.command_failure",
            severity="fail",
            message=_tail(output),
            location=None,
            auto_fixable=auto_fixable,
            fix_hint="Re-run lint fix command" if auto_fixable else None,
        )]

    def fix(self, inp: ContractInput) -> ContractInput:
        if self._fix_command:
            run_command(self._root, self._fix_command)
        return inp


class CommandTestsGate:
    """Run a configured test command; optional JUnit report for per-test failures."""

    def __init__(
        self,
        worktree_path: Path,
        base_branch: str,
        command: Sequence[str],
        report: str = "",
        report_path: str = "",
        flaky_reruns: int = 0,
    ) -> None:
        self._root = worktree_path
        self._base = base_branch
        self._command = tuple(command)
        self._report = (report or "").strip()
        self._report_path = (report_path or "").strip()
        self._flaky_reruns = flaky_reruns
        self.derived_allow_paths: list[str] = []

    def _run_cmd(self) -> tuple[int, str]:
        if self._report == "junit" and self._report_path:
            _prepare_report(self._root, self._report_path)
        return run_command(self._root, self._command)

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        self.derived_allow_paths = []
        rc, output = self._run_cmd()
        try:
            if self._report == "junit" and self._report_path:
                return self._judge_junit(rc, output)
            return self._judge_exit_code(rc, output)
        finally:
            if self._report == "junit" and self._report_path:
                _finalize_report(self._root, self._report_path)

    def _judge_exit_code(self, rc: int, output: str) -> list[Violation]:
        if rc == 0:
            return []
        if rc in _INTERRUPTED_RC:
            return [_interrupted_violation(rc)]
        if rc == 124:
            return [Violation(
                rule_id="tests.command_timeout",
                severity="fail",
                message=_tail(output),
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]
        return [Violation(
            rule_id="tests.command_failure",
            severity="fail",
            message=_tail(output),
            location=None,
            auto_fixable=False,
            fix_hint="Fix failing tests before proceeding",
        )]

    def _judge_junit(self, rc: int, output: str) -> list[Violation]:
        if rc == 0:
            return []
        if rc in _INTERRUPTED_RC:
            return [_interrupted_violation(rc)]
        if rc == 124:
            return [Violation(
                rule_id="tests.command_timeout",
                severity="fail",
                message=_tail(output),
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        report = self._root / self._report_path
        ids = read_junit_failed_ids(report)
        if ids is None or not ids:
            return [Violation(
                rule_id="tests.command_failure",
                severity="fail",
                message=_tail(output),
                location=None,
                auto_fixable=False,
                fix_hint="Fix failing tests before proceeding",
            )]

        baseline_failed = _baseline_junit_failed(
            self._root, self._base, self._command, self._report_path
        )
        baseline_unavailable = baseline_failed is None

        new_ids: list[str] = []
        preexisting_ids: list[str] = []
        for test_id in ids:
            if not baseline_unavailable and _func_id(test_id) in baseline_failed:
                preexisting_ids.append(test_id)
            else:
                new_ids.append(test_id)

        reruns = self._flaky_reruns
        flaky_ids: list[str] = []
        if reruns > 0 and new_ids:
            confirmed = set(new_ids)
            for _ in range(reruns):
                if not confirmed:
                    break
                rc2, out2 = self._run_cmd()
                if rc2 == 0:
                    confirmed = set()
                    break
                report2 = self._root / self._report_path
                rerun_ids = read_junit_failed_ids(report2)
                if rc2 in _INTERRUPTED_RC or rc2 == 124 or rerun_ids is None:
                    break
                rerun_failed = {_func_id(i) for i in rerun_ids}
                confirmed = {i for i in confirmed if _func_id(i) in rerun_failed}
            flaky_ids = [i for i in new_ids if i not in confirmed]
            new_ids = [i for i in new_ids if i in confirmed]

        if not new_ids and not flaky_ids:
            return []

        fix_hint = "Fix failing tests before proceeding"
        if preexisting_ids:
            fix_hint += (
                f"; preexisting on origin/{self._base} (non-blocking):"
                f" {', '.join(preexisting_ids)}"
            )

        violations: list[Violation] = []
        for test_id in flaky_ids:
            violations.append(Violation(
                rule_id="tests.flaky",
                severity="warn",
                message=(
                    f"{test_id} (flaky: passed in at least one of {reruns} reruns)"
                ),
                location=test_id,
                auto_fixable=False,
                fix_hint=None,
            ))
        for test_id in new_ids:
            message = test_id
            if baseline_unavailable:
                message += " baseline unavailable"
            violations.append(Violation(
                rule_id="tests.junit_failure",
                severity="fail",
                message=message,
                location=test_id,
                auto_fixable=False,
                fix_hint=fix_hint,
            ))
        return violations

    def fix(self, inp: ContractInput) -> ContractInput:
        return inp
