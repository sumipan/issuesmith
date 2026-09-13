"""PR diff scope gate — allow_paths / forbidden_pr_paths check (#3178)."""

from __future__ import annotations

import fnmatch
import re
from typing import Sequence

from ghdag.workflow.gates import Violation

from issuesmith.config import _DEFAULT_FORBIDDEN_PR_PATHS

DEFAULT_FORBIDDEN_PR_PATHS = _DEFAULT_FORBIDDEN_PR_PATHS

_FIXTURE_EXCLUDE = "tests/fixtures/**"
_VERSION_LINE_RE = re.compile(r"^[+-]version\s*=")


def _is_publish_only_pyproject(patch: str) -> bool:
    """Return True when pyproject.toml patch changes only ``version =`` lines."""
    changed = [
        line
        for line in patch.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    return bool(changed) and all(_VERSION_LINE_RE.match(line) for line in changed)


def _is_publish_only_changelog(entry: dict) -> bool:
    """Return True when CHANGELOG diff is append-only (no deletions)."""
    return int(entry.get("deletions") or 0) == 0


def _is_publish_only_change(filename: str, entry: dict | None) -> bool:
    """True for P3 publish auto-edits that must not trip allow_paths (#3216)."""
    if entry is None:
        return False
    if filename == "pyproject.toml":
        patch = entry.get("patch")
        return isinstance(patch, str) and _is_publish_only_pyproject(patch)
    if filename == "CHANGELOG.md":
        return _is_publish_only_changelog(entry)
    return False


def _entries_by_filename(
    file_entries: Sequence[dict] | None,
) -> dict[str, dict]:
    if not file_entries:
        return {}
    out: dict[str, dict] = {}
    for entry in file_entries:
        if isinstance(entry, dict):
            name = entry.get("filename")
            if isinstance(name, str) and name:
                out[name] = entry
    return out


def check_pr_diff_scope(
    filenames: list[str],
    allow_paths: list[str],
    forbidden_patterns: list[str] | None = None,
    file_entries: Sequence[dict] | None = None,
) -> list[Violation]:
    """Return violations for PR files outside allow_paths or matching forbidden patterns.

    ``tests/fixtures/**`` paths skip the forbidden-pattern check (fixture ``.jsonl``
    etc. remain allowed) but must still match ``allow_paths``.

    When ``file_entries`` is provided, publish-only edits (``pyproject.toml`` version
    line only, ``CHANGELOG.md`` append-only) are excluded from violations (#3216).
    """
    if forbidden_patterns is None:
        from issuesmith.config import get_config

        forbidden_patterns = list(get_config().forbidden_pr_paths)

    by_name = _entries_by_filename(file_entries)
    violations: list[Violation] = []
    for filename in filenames:
        if _is_publish_only_change(filename, by_name.get(filename)):
            continue

        is_fixture = fnmatch.fnmatch(filename, _FIXTURE_EXCLUDE)
        if not is_fixture:
            matched_forbidden = next(
                (pat for pat in forbidden_patterns if fnmatch.fnmatch(filename, pat)),
                None,
            )
            if matched_forbidden is not None:
                violations.append(
                    Violation(
                        rule_id="pr_diff_scope.forbidden_path",
                        severity="fail",
                        message=(
                            f"forbidden path: {filename} "
                            f"(matched {matched_forbidden!r})"
                        ),
                        location=filename,
                        auto_fixable=False,
                        fix_hint=f"git checkout main -- {filename}",
                    )
                )
                continue

        if allow_paths and not any(
            fnmatch.fnmatch(filename, pat) for pat in allow_paths
        ):
            violations.append(
                Violation(
                    rule_id="pr_diff_scope.out_of_scope",
                    severity="fail",
                    message=f"out of scope: {filename} (not in allow_paths)",
                    location=filename,
                    auto_fixable=False,
                    fix_hint=f"git checkout main -- {filename}",
                )
            )
    return violations


def filenames_from_pr_files(files: Sequence[dict] | None) -> list[str]:
    """Extract ``filename`` strings from ``pr_get()["files"]`` entries."""
    if not files:
        return []
    out: list[str] = []
    for entry in files:
        if isinstance(entry, dict):
            name = entry.get("filename")
            if isinstance(name, str) and name:
                out.append(name)
    return out
