"""PR diff scope gate — allow_paths / forbidden_pr_paths check (#3178)."""

from __future__ import annotations

import fnmatch
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Sequence

from ghdag.forge import ForgePort
from ghdag.workflow.gates import Violation

from issuesmith.config import _DEFAULT_FORBIDDEN_PR_PATHS, get_config
from issuesmith.context_hook import parse_issue_metadata
from issuesmith.forge_api import api_request

DEFAULT_FORBIDDEN_PR_PATHS = _DEFAULT_FORBIDDEN_PR_PATHS

DIFF_LINES_FALLBACK = 9999

# Fixture data may live under any tests/**/fixtures/ directory (e.g. tests/scripts/fixtures/x/a.jsonl)
_FIXTURE_EXCLUDES = ("tests/fixtures/**", "tests/*/fixtures/**", "tests/*/*/fixtures/**", "tests/*/*/*/fixtures/**")
_FIXTURE_EXCLUDE = _FIXTURE_EXCLUDES[0]  # backward compat

_DERIVED_ENTRY_RE = re.compile(r"^[^\s/][^\s]*$|^[a-zA-Z0-9_./\-*]+\*\*$")


class DerivedAllowPathsError(ValueError):
    """Raised when ``derived_allow_paths_from_result`` finds malformed entries."""


def _is_fixture_path(filename: str) -> bool:
    return any(fnmatch.fnmatch(filename, pat) for pat in _FIXTURE_EXCLUDES)
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

    ``tests/**/fixtures/**`` paths skip the forbidden-pattern check (fixture ``.jsonl``
    etc. remain allowed) but must still match ``allow_paths``.

    When ``file_entries`` is provided, publish-only edits (``pyproject.toml`` version
    line only, ``CHANGELOG.md`` append-only) are excluded from violations (#3216).
    """
    if forbidden_patterns is None:
        forbidden_patterns = list(get_config().forbidden_pr_paths)

    by_name = _entries_by_filename(file_entries)
    violations: list[Violation] = []
    for filename in filenames:
        if _is_publish_only_change(filename, by_name.get(filename)):
            continue

        is_fixture = _is_fixture_path(filename)
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


def _head_param(repo: str, branch: str) -> str | None:
    branch = branch.strip()
    if not branch:
        return None
    if ":" in branch:
        return branch
    owner = repo.split("/", 1)[0] if "/" in repo else ""
    return f"{owner}:{branch}" if owner else None


def _pulls_list_path(repo: str, branch: str) -> str | None:
    head = _head_param(repo, branch)
    if head is None:
        return None
    encoded = urllib.parse.quote(head, safe="")
    if repo:
        return f"repos/{repo}/pulls?head={encoded}&state=open"
    return f"pulls?head={encoded}&state=open"


def _list_open_pulls(client: ForgePort, repo: str) -> list[dict]:
    if not repo:
        return []
    path = f"repos/{repo}/pulls?state=open&per_page=100"
    try:
        listed = api_request(client, path)
    except Exception as exc:
        print(f"pr_scope: PR list failed ({exc})", file=sys.stderr)
        return []
    if not isinstance(listed, list):
        return []
    return [p for p in listed if isinstance(p, dict)]


def _open_pr_number_by_branch(
    client: ForgePort, repo: str, branch: str
) -> int | None:
    """Return the open PR number whose head ref matches ``branch``."""
    if not branch.strip():
        return None
    path = _pulls_list_path(repo, branch)
    if path is None:
        print(
            "pr_scope: skip PR search (no owner for head filter; "
            f"repo={repo!r} branch={branch!r})",
            file=sys.stderr,
        )
        return None
    try:
        listed = api_request(client, path)
    except Exception as exc:
        print(f"pr_scope: PR list failed ({exc})", file=sys.stderr)
        return None
    if not isinstance(listed, list) or not listed:
        return None
    first = next(
        (
            pr
            for pr in listed
            if isinstance(pr, dict)
            and isinstance(pr.get("head"), dict)
            and pr["head"].get("ref") == branch
        ),
        None,
    )
    if first is None or not isinstance(first.get("number"), int):
        return None
    return first["number"]


def _open_pr_number_by_body_refs(
    client: ForgePort, repo: str, issue_number: int
) -> int | None:
    """Fallback: open PR whose body contains ``Refs #N``."""
    refs_marker = f"Refs #{issue_number}"
    for pr in _list_open_pulls(client, repo):
        number = pr.get("number")
        if not isinstance(number, int):
            continue
        body = pr.get("body") or ""
        if refs_marker in body:
            return number
    return None


def _open_pr_number_by_issue_body(
    issue_body: str, open_pulls: list[dict]
) -> int | None:
    """Fallback: match PR number linked from issue body (``#123`` / full URL)."""
    for pr in open_pulls:
        number = pr.get("number")
        if not isinstance(number, int):
            continue
        if re.search(rf"(?<!\d)#{number}(?!\d)", issue_body):
            return number
        if re.search(rf"/pull/{number}(?:[^\d]|$)", issue_body):
            return number
    return None


def _pr_get_detail(client: ForgePort, repo: str, number: int) -> dict | None:
    try:
        detail = client.pr_get(number, repo=repo or None)
    except Exception as exc:
        print(f"pr_scope: pr_get({number}) failed ({exc})", file=sys.stderr)
        return None
    if not isinstance(detail, dict):
        return None
    return detail


def find_pr_for_branch(
    client: ForgePort,
    repo: str,
    branch: str,
    *,
    issue_number: int | None = None,
    issue_body: str | None = None,
) -> tuple[int | None, dict | None]:
    """Return ``(pr_number, pr_get detail)`` for the open PR on ``branch``.

    Head branch match is tried first. When that yields no PR, optional
    ``issue_number`` / ``issue_body`` enable PR-body ``Refs #N`` and issue-body
    PR link fallback searches (#4273).
    """
    branch = branch.strip()
    number = _open_pr_number_by_branch(client, repo, branch)
    if number is None and issue_number is not None:
        number = _open_pr_number_by_body_refs(client, repo, issue_number)
    if number is None and issue_body:
        number = _open_pr_number_by_issue_body(issue_body, _list_open_pulls(client, repo))
    if number is None:
        return None, None
    return number, _pr_get_detail(client, repo, number)


def _diff_lines_from_detail(detail: dict | None) -> int:
    if detail is None:
        return DIFF_LINES_FALLBACK
    additions = detail.get("additions")
    deletions = detail.get("deletions")
    if additions is None and deletions is None:
        return DIFF_LINES_FALLBACK
    return int(additions or 0) + int(deletions or 0)


def pr_diff_lines(
    client: ForgePort,
    repo: str,
    branch: str,
    *,
    issue_number: int | None = None,
    issue_body: str | None = None,
) -> int:
    """Return additions+deletions for the open PR on ``branch``, or 9999 if absent."""
    _number, detail = find_pr_for_branch(
        client,
        repo,
        branch,
        issue_number=issue_number,
        issue_body=issue_body,
    )
    return _diff_lines_from_detail(detail)


def unchecked_ac_count(body: str) -> int:
    """Count unchecked ``- [ ]`` items inside the configured AC section."""
    from issuesmith.gate_rules.m2 import get_unchecked_count

    return get_unchecked_count(body)


def _validate_derived_entry(entry: str) -> None:
    if not entry or entry.startswith("/") or ".." in entry.split("/"):
        raise DerivedAllowPathsError(f"invalid derived_allow_paths entry: {entry!r}")
    if not _DERIVED_ENTRY_RE.match(entry):
        raise DerivedAllowPathsError(f"invalid derived_allow_paths entry: {entry!r}")


def derived_allow_paths_from_result(path: Path | str) -> list[str]:
    """Parse ``derived_allow_paths:`` block from a P1/P2 result file.

    Missing file or block → ``[]``. Malformed entries raise ``DerivedAllowPathsError``.
    """
    if not isinstance(path, (Path, str)):
        raise TypeError(f"path must be Path or str, got {type(path)!r}")
    result_path = Path(path)
    try:
        lines = result_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    derived: list[str] = []
    in_block = False
    for line in lines:
        if line.rstrip().endswith("derived_allow_paths:"):
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if line.startswith("  - ") and stripped[2:].strip():
                entry = stripped[2:].strip()
                _validate_derived_entry(entry)
                derived.append(entry)
                continue
            in_block = False
    return derived


def allow_paths_from_issue_body(body: str) -> list[str]:
    """Extract ``allow_paths`` from issue YAML metadata."""
    try:
        meta = parse_issue_metadata(body)
    except ValueError:
        return []
    raw = meta.get("allow_paths", [])
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    return [str(p) for p in raw if p is not None and str(p).strip()]


def check_pr_scope_with_derived(
    pr_detail: dict | None,
    allow_paths: list[str],
    *,
    derived_allow_paths: list[str] | None = None,
    result_path: Path | str | None = None,
    forbidden_patterns: list[str] | None = None,
) -> list[Violation]:
    """Check PR file list against ``allow_paths`` union ``derived_allow_paths``.

    When ``result_path`` is set, derived paths are read from that result file.
    Returns concrete out-of-scope / forbidden violations (empty when clean).
    """
    if pr_detail is None:
        return []
    file_entries = pr_detail.get("files")
    filenames = filenames_from_pr_files(file_entries)
    if not filenames:
        return []
    merged = list(allow_paths)
    derived = list(derived_allow_paths or [])
    if result_path is not None:
        for path in derived_allow_paths_from_result(result_path):
            if path not in derived:
                derived.append(path)
    for path in derived:
        if path not in merged:
            merged.append(path)
    return check_pr_diff_scope(
        filenames,
        merged,
        forbidden_patterns=forbidden_patterns,
        file_entries=file_entries,
    )


__all__ = [
    "DEFAULT_FORBIDDEN_PR_PATHS",
    "DIFF_LINES_FALLBACK",
    "DerivedAllowPathsError",
    "allow_paths_from_issue_body",
    "check_pr_diff_scope",
    "check_pr_scope_with_derived",
    "derived_allow_paths_from_result",
    "filenames_from_pr_files",
    "find_pr_for_branch",
    "pr_diff_lines",
    "unchecked_ac_count",
]
