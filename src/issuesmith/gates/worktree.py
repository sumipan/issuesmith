"""Worktree gates: lint, tests, external_leak, base_freshness (#3626).

Each gate checks the worktree filesystem state and, where possible, applies a
deterministic fix (narrowing-only: ruff --fix, git ff). fix() never widens
allow_paths or adds new scope.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ghdag.workflow.gates import Violation

from issuesmith.gates.base import ContractInput

_CHANGED_FILES_EXCLUDE_PREFIXES: tuple[str, ...] = ("jobs/", "logs/", ".pipeline-state/")


def changed_files(worktree_path: Path, base_branch: str) -> list[str]:
    """Return sorted changed files vs origin/<base_branch> plus uncommitted/untracked.

    Excludes files under jobs/, logs/, .pipeline-state/, and deleted files.
    """
    committed: set[str] = set()
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"origin/{base_branch}...HEAD"],
        capture_output=True, text=True, check=False,
        cwd=str(worktree_path),
    )
    if proc.returncode == 0:
        for f in proc.stdout.splitlines():
            f = f.strip()
            if f:
                committed.add(f)

    status_files: set[str] = set()
    proc2 = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=False,
        cwd=str(worktree_path),
    )
    if proc2.returncode == 0:
        for line in proc2.stdout.splitlines():
            if len(line) < 4:
                continue
            fname = line[3:].strip()
            if not fname:
                continue
            if " -> " in fname:
                fname = fname.split(" -> ", 1)[1].strip()
            status_files.add(fname)

    all_files = committed | status_files
    return sorted(
        f for f in all_files
        if not any(f.startswith(p) for p in _CHANGED_FILES_EXCLUDE_PREFIXES)
        and (worktree_path / f).exists()
    )


class LintGate:
    """Run ruff check on changed .py files in the worktree; fix via ruff --fix."""

    def __init__(
        self, worktree_path: Path, allow_paths: list[str], base_branch: str = "main"
    ) -> None:
        self._root = worktree_path
        self._paths = allow_paths
        self._base_branch = base_branch

    def _targets(self) -> list[str]:
        """Return .py files from changed_files (not allow_paths globs)."""
        try:
            files = changed_files(self._root, self._base_branch)
        except Exception:
            files = [p for p in self._paths if (self._root / p).exists()]
        return [str(self._root / f) for f in files if f.endswith(".py")]

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


_PYTEST_TIMEOUT_ENV = "ISSUESMITH_PYTEST_TIMEOUT_SEC"
_PYTEST_TIMEOUT_DEFAULT_SEC = 1500.0


def _pytest_timeout_sec() -> float:
    raw = (os.environ.get(_PYTEST_TIMEOUT_ENV) or "").strip()
    try:
        return float(raw) if raw else _PYTEST_TIMEOUT_DEFAULT_SEC
    except ValueError:
        return _PYTEST_TIMEOUT_DEFAULT_SEC


def _run_pytest(root: Path, args: list[str], *, timeout: float | None = None) -> tuple[int, str]:
    """Run pytest with `-q -rfE --tb=no` in `root`; prepend `root/src` to PYTHONPATH if present.

    A run that exceeds ``timeout`` (default ``ISSUESMITH_PYTEST_TIMEOUT_SEC`` or 1500 s)
    returns exit code 124 with a one-line message instead of hanging until the
    outer task timeout kills the whole step (nexus #3864).
    """
    env = dict(os.environ)
    src = root / "src"
    if src.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (str(src) + ":" + existing) if existing else str(src)
    limit = timeout if timeout is not None else _pytest_timeout_sec()
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", "-q", "-rfE", "--tb=no", "-p", "no:cacheprovider", *args],
            capture_output=True, text=True, check=False,
            cwd=str(root), env=env, timeout=limit,
        )
    except subprocess.TimeoutExpired as exc:
        partial = ((exc.stdout or b"") if isinstance(exc.stdout, bytes) else (exc.stdout or "").encode())
        tail = partial.decode(errors="replace")[-2000:]
        return 124, f"pytest timed out after {limit:.0f} s\n{tail}"
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


_DERIVED_TESTS_PREFIX = "tests/"
_DIFF_DEF_RE = re.compile(r"^[+-]\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)")


def _exists_on_base(root: Path, base_branch: str, path: str) -> bool:
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"origin/{base_branch}:{path}"],
        capture_output=True, check=False, cwd=str(root),
    )
    return proc.returncode == 0


def _src_module_name(path: str) -> str | None:
    """`src/a/b.py` → `a.b`; `src/a/__init__.py` → `a`; None outside src/*.py."""
    if not (path.startswith("src/") and path.endswith(".py")):
        return None
    parts = path[len("src/"):-len(".py")].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or None


def _diff_public_names(root: Path, base_branch: str, path: str) -> set[str]:
    """Public def/class names on +/- lines of `git diff -U0 origin/<base> -- <path>`."""
    proc = subprocess.run(
        ["git", "diff", "-U0", f"origin/{base_branch}", "--", path],
        capture_output=True, text=True, check=False, cwd=str(root),
    )
    if proc.returncode != 0:
        return set()
    names: set[str] = set()
    for line in proc.stdout.splitlines():
        if line.startswith(("+++", "---")):
            continue
        m = _DIFF_DEF_RE.match(line)
        if m and not m.group(1).startswith("_"):
            names.add(m.group(1))
    return names


def _reference_keys(
    root: Path, base_branch: str, changed: list[str]
) -> tuple[set[str], set[str]]:
    """Return (substring keys, word keys) derived from the changed files (condition C)."""
    # Same key filter as scope_coupling (#3647). Module access: scope_coupling.py is outside
    # this change's scope, so the helper cannot be made public here.
    from issuesmith.gate_rules import scope_coupling

    _is_valid_key = scope_coupling._is_valid_key
    substr_keys: set[str] = set()
    word_keys: set[str] = set()
    for path in changed:
        substr_keys.add(path)
        module = _src_module_name(path)
        if module:
            substr_keys.add(module)
        stem = Path(path).stem
        if _is_valid_key(stem):
            word_keys.add(stem)
        if path.endswith(".py"):
            word_keys.update(
                n for n in _diff_public_names(root, base_branch, path) if _is_valid_key(n)
            )
    return substr_keys, word_keys


def derive_test_allow_paths(
    root: Path,
    base_branch: str,
    failed_ids: list[str],
    changed: list[str],
    allow_paths: list[str],
) -> list[str]:
    """Return test files repair may edit beyond allow_paths (#3756).

    A file qualifies when (A) it is under ``tests/``, (B) it has a newly failing ID in
    ``failed_ids`` and exists on ``origin/<base_branch>``, and (C) its text references
    one of the keys derived from ``changed``. Files already matching allow_paths are
    excluded. Result is sorted and de-duplicated.
    """
    candidates: set[str] = set()
    for test_id in failed_ids:
        path = test_id.split("::")[0]
        if not path.startswith(_DERIVED_TESTS_PREFIX):
            continue
        if any(fnmatch.fnmatch(path, pat) for pat in allow_paths):
            continue
        candidates.add(path)
    if not candidates:
        return []

    substr_keys, word_keys = _reference_keys(root, base_branch, changed)
    word_res = [re.compile(rf"\b{re.escape(k)}\b") for k in sorted(word_keys)]

    derived: list[str] = []
    for path in sorted(candidates):
        if not _exists_on_base(root, base_branch, path):
            continue
        try:
            text = (root / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(k in text for k in substr_keys) or any(r.search(text) for r in word_res):
            derived.append(path)
    return derived


def _mapped_test_paths(root: Path, changed: list[str]) -> list[str]:
    """Return existing test files that map to changed sources (tests/**/test_<stem>*.py).

    Changed files under tests/ are not mapped on their own: a failure there may sit next
    to preexisting failures, and only the full run reports those (non-blocking) and every
    new failure. They are still picked up when a changed source maps to them.
    """
    mapped: set[str] = set()
    for path in changed:
        p = Path(path)
        if p.suffix != ".py" or path.startswith("tests/"):
            continue
        for hit in root.glob(f"tests/**/test_{p.stem}*.py"):
            mapped.add(hit.relative_to(root).as_posix())
    return sorted(m for m in mapped if (root / m).is_file())


# rc of a pytest killed by SIGTERM/SIGKILL: negative from subprocess, 128+N via a shell.
_INTERRUPTED_RC: frozenset[int] = frozenset({143, 137, -15, -9})
_SUMMARY_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")


def _log_run(mode: str, rc: int, elapsed: float) -> None:
    print(f"[tests] {mode}: rc={rc} elapsed={elapsed:.2f}s", file=sys.stderr)


def _log_durations(output: str) -> None:
    """Echo the `slowest` section and the final summary line of pytest output to stderr."""
    lines = output.splitlines()
    section: list[str] = []
    for i, line in enumerate(lines):
        if "slowest" in line and line.startswith("="):
            section.append(line)
            for rest in lines[i + 1:]:
                if rest.startswith("="):
                    break
                section.append(rest)
            break
    summary = next((ln for ln in reversed(lines) if _SUMMARY_RE.search(ln)), None)
    if summary is not None:
        section.append(summary)
    if section:
        print("\n".join(section), file=sys.stderr)


def _interrupted(rc: int) -> Violation:
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


_SKIP_NAMES: frozenset[str] = frozenset({"skip", "skipif", "xfail", "skipTest"})


def _test_shape(tree: ast.AST) -> tuple[list[str], int, int]:
    """Return (test function names, assert count, skip/xfail reference count)."""
    funcs: list[str] = []
    asserts = 0
    skips = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test"):
                funcs.append(node.name)
        elif isinstance(node, ast.Assert):
            asserts += 1
        elif isinstance(node, ast.Attribute) and node.attr in _SKIP_NAMES:
            skips += 1
        elif isinstance(node, ast.Name) and node.id in _SKIP_NAMES:
            skips += 1
    return funcs, asserts, skips


def _weakened(path: str, message: str) -> Violation:
    return Violation(
        rule_id="derived_allow.test_weakened",
        severity="fail",
        message=f"{path}: {message}",
        location=path,
        auto_fixable=False,
        fix_hint="Fix the expectation without removing tests or asserts",
    )


def check_derived_test_guard(
    root: Path, base_branch: str, paths: list[str]
) -> list[Violation]:
    """Fail when a derived-allowed test file loses tests/asserts or gains skip/xfail."""
    violations: list[Violation] = []
    for path in paths:
        proc = subprocess.run(
            ["git", "show", f"origin/{base_branch}:{path}"],
            capture_output=True, text=True, check=False, cwd=str(root),
        )
        try:
            head_text = (root / path).read_text(encoding="utf-8")
            head = _test_shape(ast.parse(head_text))
        except (OSError, SyntaxError, ValueError) as exc:
            violations.append(_weakened(path, f"cannot parse working tree file ({exc})"))
            continue
        if proc.returncode != 0:
            continue
        try:
            base = _test_shape(ast.parse(proc.stdout))
        except (SyntaxError, ValueError):
            continue
        base_funcs, base_asserts, base_skips = base
        head_funcs, head_asserts, head_skips = head
        if len(head_funcs) < len(base_funcs) or head_asserts < base_asserts:
            removed = sorted(set(base_funcs) - set(head_funcs))
            violations.append(_weakened(
                path,
                f"tests {len(base_funcs)}->{len(head_funcs)},"
                f" asserts {base_asserts}->{head_asserts};"
                f" removed: {', '.join(removed) or '(none)'}",
            ))
        if head_skips > base_skips:
            violations.append(Violation(
                rule_id="derived_allow.test_skipped",
                severity="fail",
                message=f"{path}: skip/xfail references {base_skips}->{head_skips}",
                location=path,
                auto_fixable=False,
                fix_hint="Do not skip or xfail tests to make them pass",
            ))
    return violations


class TestsGate:
    """Run pytest in the worktree with baseline comparison against `origin/<base_branch>`.

    - Runs tests mapped from changed files first (`-x`); new failures there skip the full run.
    - Runs all tests (no -x) with `--durations` and collects all failure IDs.
    - rc from SIGTERM/SIGKILL → `tests.interrupted` (rerun only, no code change).
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
        allow_paths: list[str] | None = None,
    ) -> None:
        self._root = worktree_path
        self._test_paths = test_paths or ["tests"]
        self._base = base_branch
        self._allow_paths = list(allow_paths or [])
        # Side product of check() (#3756): tests allowed for repair beyond allow_paths.
        self.derived_allow_paths: list[str] = []

    def _derive(self, new_ids: list[str]) -> list[str]:
        """Compute derived allow_paths for newly failing tests (empty when disabled)."""
        from issuesmith.config import get_config

        if not new_ids or not get_config().derived_allow.enabled:
            return []
        changed = changed_files(self._root, self._base)
        return derive_test_allow_paths(
            self._root, self._base, new_ids, changed, self._allow_paths
        )

    def _new_ids(self, ids: list[str]) -> list[str] | None:
        """Return IDs that do not fail on base, or None when the baseline is unavailable."""
        baseline_failed = _baseline_failed_func_ids(self._root, self._base, ids)
        if baseline_failed is None:
            return None
        return [i for i in ids if _func_id(i) not in baseline_failed]

    def _run(self, mode: str, args: list[str]) -> tuple[int, str]:
        started = time.monotonic()
        rc, output = _run_pytest(self._root, args)
        _log_run(mode, rc, time.monotonic() - started)
        return rc, output

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        self.derived_allow_paths = []
        try:
            changed = changed_files(self._root, self._base)
        except Exception:
            changed = []
        mapped = _mapped_test_paths(self._root, changed)
        if mapped:
            rc, output = self._run("mapped", [*mapped, "-x"])
            if rc != 0:
                violations = self._judge(rc, output)
                if violations:
                    return violations

        targets = [str(self._root / p) for p in self._test_paths]
        rc, output = self._run("full", [*targets, "--durations=20", "--durations-min=1.0"])
        _log_durations(output)
        return self._judge(rc, output)

    def _judge(self, rc: int, output: str) -> list[Violation]:
        """Turn a pytest result into violations; [] when all failures are preexisting."""
        if rc == 0:
            return []

        if rc in _INTERRUPTED_RC:
            return [_interrupted(rc)]

        if rc not in (0, 1):
            # Collection errors stay blocking, but newly broken files still get derived.
            collect_ids = _parse_failed_ids(output)
            if collect_ids:
                new_collect_ids = self._new_ids(collect_ids)
                if new_collect_ids:
                    self.derived_allow_paths = self._derive(new_collect_ids)
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

        if not baseline_unavailable:
            self.derived_allow_paths = self._derive(new_ids)
        derived = set(self.derived_allow_paths)

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
            hint = fix_hint
            if location in derived:
                hint += f"; derived_allow: {location}"
            violations.append(Violation(
                rule_id="tests.pytest_failure",
                severity="fail",
                message=message,
                location=location,
                auto_fixable=False,
                fix_hint=hint,
            ))

        return violations

    def fix(self, inp: ContractInput) -> ContractInput:
        return inp  # no deterministic fix for test failures


class ExternalLeakGate:
    """Check for accidental secrets or external references in changed files."""

    _LEAK_PATTERNS: tuple[str, ...] = (
        r"ghp_[A-Za-z0-9]{36}",
        r"AKIA[0-9A-Z]{16}",
        r"sk-[A-Za-z0-9]{48}",
    )

    def __init__(
        self, worktree_path: Path, allow_paths: list[str], base_branch: str = "main"
    ) -> None:
        self._root = worktree_path
        self._paths = allow_paths
        self._base_branch = base_branch

    def check(self, body: str, labels: list[str]) -> list[Violation]:
        try:
            files_to_check = changed_files(self._root, self._base_branch)
        except Exception:
            files_to_check = [p for p in self._paths if (self._root / p).exists()]
        violations = []
        for p in files_to_check:
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
        """Catch up with origin/<base>: fast-forward, else rebase our commits on top.

        A silent ``--ff-only`` failure used to leave the branch behind while the loop
        believed the fix succeeded; with a base that moves every few seconds the
        requires loop then re-ran every gate until the task timeout (2026-09-25,
        nexus #3865 / #3864). A rebase that conflicts is aborted and reported.
        """
        ff = subprocess.run(
            ["git", "merge", "--ff-only", f"origin/{self._base}"],
            capture_output=True, text=True, check=False,
            cwd=str(self._root),
        )
        if ff.returncode == 0:
            return inp
        rebase = subprocess.run(
            ["git", "rebase", f"origin/{self._base}"],
            capture_output=True, text=True, check=False,
            cwd=str(self._root),
        )
        if rebase.returncode == 0:
            return inp
        subprocess.run(
            ["git", "rebase", "--abort"],
            capture_output=True, text=True, check=False,
            cwd=str(self._root),
        )
        detail = (rebase.stderr or rebase.stdout or "").strip()[-400:]
        raise RuntimeError(
            f"base_freshness auto-fix failed: ff-only and rebase onto origin/{self._base} both failed: {detail}"
        )


def _build_lint(worktree_path: Path, allow_paths: list[str], base_branch: str) -> LintGate:
    return LintGate(worktree_path, allow_paths, base_branch)


def _build_tests(worktree_path: Path, allow_paths: list[str], base_branch: str) -> TestsGate:
    return TestsGate(worktree_path, base_branch=base_branch, allow_paths=allow_paths)


def _build_external_leak(
    worktree_path: Path, allow_paths: list[str], base_branch: str
) -> ExternalLeakGate:
    return ExternalLeakGate(worktree_path, allow_paths, base_branch)


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
    "changed_files",
    "derive_test_allow_paths",
    "check_derived_test_guard",
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
