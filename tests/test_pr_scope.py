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
