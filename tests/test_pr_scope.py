"""Unit tests for issuesmith.pr_scope.check_pr_diff_scope (#3178).

PR file fixtures use the shape of ``GitHubClient.pr_get()["files"]``
captured 2026-09-13 from ``pr_get(3109, repo="sumipan/nexus")``
(CLAUDE.md §10 / AGENTS.md §16):

  keys per file: additions, blob_url, changes, contents_url, deletions,
  filename, patch, raw_url, sha, status
"""

from __future__ import annotations

from issuesmith.pr_scope import DEFAULT_FORBIDDEN_PR_PATHS, check_pr_diff_scope

# Minimal real-shape file entries (filename + status only; values from PR #3109).
_FILE_JOBS_EXEC = {"filename": "jobs/exec.jsonl", "status": "modified", "additions": 1, "deletions": 0}
_FILE_DOCS = {"filename": "docs/README.md", "status": "modified", "additions": 1, "deletions": 0}
_FILE_SRC = {"filename": "src/issuesmith/pr_scope.py", "status": "added", "additions": 10, "deletions": 0}
_FILE_TEST = {"filename": "tests/test_pr_scope.py", "status": "added", "additions": 5, "deletions": 0}
_FILE_FIXTURE_JSONL = {
    "filename": "tests/fixtures/foo.jsonl",
    "status": "added",
    "additions": 1,
    "deletions": 0,
}


def _filenames(*files: dict) -> list[str]:
    return [f["filename"] for f in files]


def test_forbidden_jobs_exec_jsonl_returns_forbidden_path_violation() -> None:
    filenames = _filenames(_FILE_JOBS_EXEC, _FILE_SRC)
    violations = check_pr_diff_scope(
        filenames,
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.forbidden_path"
    assert "jobs/exec.jsonl" in violations[0].message


def test_out_of_scope_docs_returns_out_of_scope_violation() -> None:
    filenames = _filenames(_FILE_DOCS, _FILE_SRC)
    violations = check_pr_diff_scope(
        filenames,
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.out_of_scope"
    assert "docs/README.md" in violations[0].message


def test_allow_paths_only_returns_empty() -> None:
    filenames = _filenames(_FILE_SRC, _FILE_TEST)
    violations = check_pr_diff_scope(
        filenames,
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
    )
    assert violations == []


def test_fixtures_jsonl_excluded_from_forbidden_patterns() -> None:
    filenames = _filenames(_FILE_FIXTURE_JSONL)
    violations = check_pr_diff_scope(
        filenames,
        allow_paths=["tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
    )
    assert violations == []


# --- #3216 AC-5 / AC-6: publish-only pyproject / CHANGELOG exclusion ---
# Real patch from issuesmith PR #44 (implements nexus #3130), captured 2026-09-13:
#   repos/sumipan/issuesmith/pulls/44/files → pyproject.toml version-only hunk
# CHANGELOG append-only shape from issuesmith PR #43.

_FILE_PYPROJECT_VERSION_ONLY = {
    "sha": "cd6991aa70fc0041bfec55966d6dff4e15f5f61a",
    "filename": "pyproject.toml",
    "status": "modified",
    "additions": 1,
    "deletions": 1,
    "changes": 2,
    "patch": (
        '@@ -4,7 +4,7 @@ build-backend = "setuptools.build_meta"\n'
        " \n"
        " [project]\n"
        ' name = "issuesmith"\n'
        '-version = "0.27.0"\n'
        '+version = "0.28.0"\n'
        ' description = "GitHub Issue label-driven workflow framework on ghdag"\n'
        ' readme = "README.md"\n'
        ' license = "MIT"'
    ),
}

_FILE_PYPROJECT_EXTRA_CHANGE = {
    "filename": "pyproject.toml",
    "status": "modified",
    "additions": 2,
    "deletions": 1,
    "changes": 3,
    "patch": (
        '@@ -4,8 +4,9 @@ build-backend = "setuptools.build_meta"\n'
        " \n"
        " [project]\n"
        ' name = "issuesmith"\n'
        '-version = "0.27.0"\n'
        '+version = "0.28.0"\n'
        '+description = "changed"\n'
        ' readme = "README.md"\n'
    ),
}

_FILE_CHANGELOG_APPEND_ONLY = {
    "filename": "CHANGELOG.md",
    "status": "modified",
    "additions": 4,
    "deletions": 0,
    "changes": 4,
    "patch": (
        "@@ -8,6 +8,10 @@ The format is based on [Keep a Changelog]"
        "(https://keepachangelog.com/en/1.1.0/).\n"
        " \n"
        " ### Added\n"
        " \n"
        "+- `pr_diff_scope` gate\n"
    ),
}


def test_publish_only_pyproject_version_excluded_from_out_of_scope() -> None:
    """AC-6: version 行のみの pyproject.toml 差分は PASS（違反なし）。"""
    entries = [_FILE_PYPROJECT_VERSION_ONLY]
    violations = check_pr_diff_scope(
        _filenames(*entries),
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
        file_entries=entries,
    )
    assert violations == []


def test_pyproject_non_version_change_still_out_of_scope() -> None:
    """AC-6: version 以外の pyproject 変更は FAIL。"""
    entries = [_FILE_PYPROJECT_EXTRA_CHANGE]
    violations = check_pr_diff_scope(
        _filenames(*entries),
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
        file_entries=entries,
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.out_of_scope"
    assert "pyproject.toml" in (violations[0].location or "")


def test_publish_only_changelog_append_excluded_from_out_of_scope() -> None:
    """AC-5: CHANGELOG 追記のみ（deletions=0）は PASS。"""
    entries = [_FILE_CHANGELOG_APPEND_ONLY]
    violations = check_pr_diff_scope(
        _filenames(*entries),
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
        file_entries=entries,
    )
    assert violations == []


def test_pyproject_without_file_entries_still_out_of_scope() -> None:
    """file_entries 無しでは従来どおりファイル名だけで判定する。"""
    violations = check_pr_diff_scope(
        ["pyproject.toml"],
        allow_paths=["src/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.out_of_scope"
