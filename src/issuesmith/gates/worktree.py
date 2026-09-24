"""Worktree gates: lint, tests, external_leak, base_freshness (#3626).

Each gate checks the worktree filesystem state and, where possible, applies a
deterministic fix (narrowing-only: ruff --fix, git ff). fix() never widens
allow_paths or adds new scope.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from ghdag.workflow.gates import Violation

from issuesmith.gates.base import ContractInput


class LintGate:
    """Run ruff check on allow_paths in the worktree; fix via ruff --fix."""

    def __init__(self, worktree_path: Path, allow_paths: list[str]) -> None:
        self._root = worktree_path
        self._paths = allow_paths

    def _targets(self) -> list[str]:
        return [str(self._root / p) for p in self._paths if (self._root / p).exists()]

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        targets = self._targets()
        if not targets:
            return []
        proc = subprocess.run(
            ["ruff", "check", "--output-format=json", *targets],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode == 0:
            return []
        try:
            errors = json.loads(proc.stdout or "[]")
        except Exception:
            errors = []
        if not errors:
            return [Violation(
                rule_id="lint.ruff_error",
                severity="fail",
                message=(proc.stderr or "ruff check failed").strip()[:400],
                location=None,
                auto_fixable=True,
                fix_hint="ruff check --fix",
            )]
        return [
            Violation(
                rule_id=f"lint.{e.get('code', 'unknown')}",
                severity="fail",
                message=(
                    f"{e.get('filename', '')}:{e.get('row', '')}: {e.get('message', '')}"
                ),
                location=e.get("filename"),
                auto_fixable=True,
                fix_hint="ruff check --fix",
            )
            for e in errors
        ]

    def fix(self, inp: ContractInput) -> ContractInput:
        targets = self._targets()
        if targets:
            subprocess.run(
                ["ruff", "check", "--fix", *targets],
                capture_output=True, check=False,
            )
        return inp


def _run_pytest(root: Path, args: list[str]) -> tuple[int, str]:
    """Run pytest with `-q -rfE --tb=no` in `root`; prepend `root/src` to PYTHONPATH if present."""
    env = dict(os.environ)
    src = root / "src"
    if src.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (str(src) + ":" + existing) if existing else str(src)
    proc = subprocess.run(
        ["python", "-m", "pytest", "-q", "-rfE", "--tb=no", "-p", "no:cacheprovider", *args],
        capture_output=True, text=True, check=False,
        cwd=str(root), env=env,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _parse_failed_ids(output: str) -> list[str]:
    """Extract unique test IDs from `FAILED`/`ERROR` lines; preserves spaces in parametrize IDs."""
    seen: set[str] = set()
    ids: list[str] = []
    for line in output.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            rest = line.split(" ", 1)[1]
            test_id = rest.split(" - ")[0]
            if test_id not in seen:
                seen.add(test_id)
                ids.append(test_id)
    return ids


def _func_id(test_id: str) -> str:
    """Strip trailing `[...]` parameters; baseline comparison is function-level."""
    return re.sub(r"\[.*\]$", "", test_id)


def _baseline_failed_func_ids(
    root: Path, base_branch: str, ids: list[str]
) -> set[str] | None:
    """Return func-level IDs that also fail on `origin/<base_branch>`, or None if unavailable."""
    tmp = tempfile.mkdtemp(dir=str(root.parent))
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", tmp,
             f"origin/{base_branch}"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None

        # Only run IDs whose file exists in baseline; convert to function-level IDs.
        baseline_func_ids: list[str] = []
        seen: set[str] = set()
        for test_id in ids:
            file_path = test_id.split("::")[0]
            if (Path(tmp) / file_path).exists():
                fid = _func_id(test_id)
                if fid not in seen:
                    seen.add(fid)
                    baseline_func_ids.append(fid)

        if not baseline_func_ids:
            return set()

        rc, output = _run_pytest(Path(tmp), baseline_func_ids)
        if rc >= 2:
            return None

        return {_func_id(i) for i in _parse_failed_ids(output)}
    except Exception:
        return None
    finally:
        subprocess.run(
            ["git", "-C", str(root), "worktree", "remove", "--force", tmp],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "-C", str(root), "worktree", "prune"],
            capture_output=True, check=False,
        )
        shutil.rmtree(tmp, ignore_errors=True)


class TestsGate:
    """Run pytest in the worktree with baseline comparison against `origin/<base_branch>`.

    - Runs all tests (no -x) and collects all failure IDs.
    - Failures that also fail on `origin/<base_branch>` are non-blocking (preexisting).
    - rc=2 (collection error) or rc=1 with no parseable IDs → fail-safe blocking violation.

    Assumes this gate runs outside a pytest session (dispatch process), not inside the nexus
    pytest harness, so tests/harness/pollution.py will not see worktree diffs from here.
    """

    def __init__(
        self,
        worktree_path: Path,
        test_paths: list[str] | None = None,
        base_branch: str = "main",
    ) -> None:
        self._root = worktree_path
        self._test_paths = test_paths or ["tests"]
        self._base = base_branch

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        targets = [str(self._root / p) for p in self._test_paths]
        rc, output = _run_pytest(self._root, targets)

        if rc == 0:
            return []

        if rc not in (0, 1):
            return [Violation(
                rule_id="tests.collection_error",
                severity="fail",
                message=output[-500:],
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        ids = _parse_failed_ids(output)
        if not ids:
            return [Violation(
                rule_id="tests.collection_error",
                severity="fail",
                message=output[-500:],
                location=None,
                auto_fixable=False,
                fix_hint=None,
            )]

        baseline_failed = _baseline_failed_func_ids(self._root, self._base, ids)
        baseline_unavailable = baseline_failed is None

        new_ids: list[str] = []
        preexisting_ids: list[str] = []
        for test_id in ids:
            if not baseline_unavailable and _func_id(test_id) in baseline_failed:  # type: ignore[operator]
                preexisting_ids.append(test_id)
            else:
                new_ids.append(test_id)

        if not new_ids:
            return []

        fix_hint = "Fix failing tests before proceeding"
        if preexisting_ids:
            fix_hint += (
                f"; preexisting on origin/{self._base} (non-blocking):"
                f" {', '.join(preexisting_ids)}"
            )

        violations: list[Violation] = []
        for test_id in new_ids:
            location = test_id.split("::")[0] if "::" in test_id else None
            message = test_id
            for line in output.splitlines():
                if _parse_failed_ids(line) == [test_id]:
                    message = line
                    break
            if baseline_unavailable:
                message += " baseline unavailable"
            violations.append(Violation(
                rule_id="tests.pytest_failure",
                severity="fail",
                message=message,
                location=location,
                auto_fixable=False,
                fix_hint=fix_hint,
            ))

        return violations

    def fix(self, inp: ContractInput) -> ContractInput:
        return inp  # no deterministic fix for test failures


class ExternalLeakGate:
    """Check for accidental secrets or external references in allow_paths."""

    _LEAK_PATTERNS: tuple[str, ...] = (
        r"ghp_[A-Za-z0-9]{36}",
        r"AKIA[0-9A-Z]{16}",
        r"sk-[A-Za-z0-9]{48}",
    )

    def __init__(self, worktree_path: Path, allow_paths: list[str]) -> None:
        self._root = worktree_path
        self._paths = allow_paths

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        violations = []
        for p in self._paths:
            full = self._root / p
            if not full.exists() or not full.is_file():
                continue
            try:
                text = full.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pat in self._LEAK_PATTERNS:
                if re.search(pat, text):
                    violations.append(Violation(
                        rule_id="external_leak.secret_pattern",
                        severity="fail",
                        message=f"{p}: possible secret matched pattern {pat!r}",
                        location=str(p),
                        auto_fixable=False,
                        fix_hint="Remove or redact the secret before proceeding",
                    ))
                    break
        return violations

    def fix(self, inp: ContractInput) -> ContractInput:
        return inp  # cannot auto-fix secrets


class BaseFreshnessGate:
    """Check that the worktree branch is up-to-date with origin/<base>; fix via ff-merge."""

    def __init__(self, worktree_path: Path, base_branch: str) -> None:
        self._root = worktree_path
        self._base = base_branch

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        proc = subprocess.run(
            ["git", "fetch", "origin", self._base],
            capture_output=True, text=True, check=False,
            cwd=str(self._root),
        )
        if proc.returncode != 0:
            return [Violation(
                rule_id="base_freshness.fetch_failed",
                severity="fail",
                message=(
                    f"git fetch origin {self._base} failed: {proc.stderr.strip()}"
                ),
                location=None,
                auto_fixable=False,
                fix_hint=f"Manually fetch origin/{self._base}",
            )]
        proc2 = subprocess.run(
            ["git", "rev-list", "--count", f"HEAD..origin/{self._base}"],
            capture_output=True, text=True, check=False,
            cwd=str(self._root),
        )
        try:
            behind = int(proc2.stdout.strip() or "0")
        except ValueError:
            behind = 0
        if behind == 0:
            return []
        return [Violation(
            rule_id="base_freshness.behind_base",
            severity="fail",
            message=f"Branch is {behind} commit(s) behind origin/{self._base}",
            location=None,
            auto_fixable=True,
            fix_hint=f"git merge --ff-only origin/{self._base}",
        )]

    def fix(self, inp: ContractInput) -> ContractInput:
        subprocess.run(
            ["git", "merge", "--ff-only", f"origin/{self._base}"],
            capture_output=True, check=False,
            cwd=str(self._root),
        )
        return inp


def _build_lint(worktree_path: Path, allow_paths: list[str], base_branch: str) -> LintGate:
    return LintGate(worktree_path, allow_paths)


def _build_tests(worktree_path: Path, allow_paths: list[str], base_branch: str) -> TestsGate:
    return TestsGate(worktree_path, base_branch=base_branch)


def _build_external_leak(
    worktree_path: Path, allow_paths: list[str], base_branch: str
) -> ExternalLeakGate:
    return ExternalLeakGate(worktree_path, allow_paths)


def _build_base_freshness(
    worktree_path: Path, allow_paths: list[str], base_branch: str
) -> BaseFreshnessGate:
    return BaseFreshnessGate(worktree_path, base_branch)


# Maps gate id → factory(worktree_path, allow_paths, base_branch).
# Replaces WORKTREE_GATE_IDS: all worktree gates are discoverable and buildable from here.
WORKTREE_GATES: dict[str, object] = {
    "lint": _build_lint,
    "tests": _build_tests,
    "external_leak": _build_external_leak,
    "base_freshness": _build_base_freshness,
}

__all__ = [
    "LintGate",
    "TestsGate",
    "ExternalLeakGate",
    "BaseFreshnessGate",
    "WORKTREE_GATES",
    "_run_pytest",
    "_parse_failed_ids",
    "_func_id",
    "_baseline_failed_func_ids",
]
