"""Worktree gates: lint, tests, external_leak, base_freshness (#3626).

Each gate checks the worktree filesystem state and, where possible, applies a
deterministic fix (narrowing-only: ruff --fix, git ff). fix() never widens
allow_paths or adds new scope.
"""
from __future__ import annotations

import json
import re
import subprocess
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


class TestsGate:
    """Run pytest baseline in the worktree; no deterministic fix."""

    def __init__(self, worktree_path: Path, test_paths: list[str] | None = None) -> None:
        self._root = worktree_path
        self._test_paths = test_paths or ["tests"]

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        targets = [str(self._root / p) for p in self._test_paths]
        proc = subprocess.run(
            ["python", "-m", "pytest", *targets, "-x", "-q", "--tb=line"],
            capture_output=True, text=True, check=False,
            cwd=str(self._root),
        )
        if proc.returncode == 0:
            return []
        output = (proc.stdout or proc.stderr or "pytest failed").strip()
        return [Violation(
            rule_id="tests.pytest_failure",
            severity="fail",
            message=output[:500],
            location=None,
            auto_fixable=False,
            fix_hint="Fix failing tests before proceeding",
        )]

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
    return TestsGate(worktree_path)


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

__all__ = ["LintGate", "TestsGate", "ExternalLeakGate", "BaseFreshnessGate", "WORKTREE_GATES"]
