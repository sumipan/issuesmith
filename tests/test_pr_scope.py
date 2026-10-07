"""Unit tests for issuesmith.pr_scope (#3178 / #4273).

PR file fixtures use the shape of ``GitHubClient.pr_get()["files"]``
captured 2026-09-13 from ``pr_get(3109, repo="sumipan/nexus")``
(CLAUDE.md §10 / AGENTS.md §16):

  keys per file: additions, blob_url, changes, contents_url, deletions,
  filename, patch, raw_url, sha, status
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from issuesmith.pr_scope import (
    DEFAULT_FORBIDDEN_PR_PATHS,
    DerivedAllowPathsError,
    allow_paths_from_issue_body,
    check_pr_diff_scope,
    check_pr_scope_with_derived,
    derived_allow_paths_from_result,
    find_pr_for_branch,
    normalize_allow_path,
    pr_diff_lines,
    unchecked_ac_count,
)

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


def test_nested_fixtures_jsonl_excluded_from_forbidden_patterns() -> None:
    """tests/<pkg>/fixtures/** (nexus #4035: tests/scripts/fixtures/memory_migrate/legacy_persona.jsonl)."""
    from issuesmith.pr_scope import check_pr_diff_scope

    files = ["tests/scripts/fixtures/memory_migrate/legacy_persona.jsonl", "tests/a/b/fixtures/x.jsonl"]
    violations = check_pr_diff_scope(files, ["tests/**"], ["*.jsonl"])
    assert violations == []
    # a .jsonl outside any fixtures/ directory is still forbidden
    assert check_pr_diff_scope(["tests/scripts/data.jsonl"], ["tests/**"], ["*.jsonl"])


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
    """AC-6: pyproject.toml diff with only the version line is PASS (no violation)."""
    entries = [_FILE_PYPROJECT_VERSION_ONLY]
    violations = check_pr_diff_scope(
        _filenames(*entries),
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
        file_entries=entries,
    )
    assert violations == []


def test_pyproject_non_version_change_still_out_of_scope() -> None:
    """AC-6: non-version pyproject changes are FAIL."""
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
    """AC-5: CHANGELOG append-only (deletions=0) is PASS."""
    entries = [_FILE_CHANGELOG_APPEND_ONLY]
    violations = check_pr_diff_scope(
        _filenames(*entries),
        allow_paths=["src/**", "tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
        file_entries=entries,
    )
    assert violations == []


def test_pyproject_without_file_entries_still_out_of_scope() -> None:
    """Without file_entries, fall back to filename-only checks as before."""
    violations = check_pr_diff_scope(
        ["pyproject.toml"],
        allow_paths=["src/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.out_of_scope"


# --- #4273: CP2 helpers moved to pr_scope ---

PR_LIST_SUCCESS_JSON = json.dumps(
    [
        {
            "number": 3180,
            "state": "open",
            "head": {
                "label": "sumipan:feat/issue-3172-32b12432-diary",
                "ref": "feat/issue-3172-32b12432-diary",
            },
        }
    ],
)

PR_GET_IN_SCOPE_JSON = json.dumps(
    {
        "number": 3180,
        "additions": 3,
        "deletions": 2,
        "files": [
            {"filename": "src/issuesmith/pr_scope.py", "status": "added"},
        ],
    },
)

PR_GET_FORBIDDEN_JSON = json.dumps(
    {
        "number": 3180,
        "additions": 1,
        "deletions": 0,
        "files": [
            {"filename": "jobs/exec.jsonl", "status": "modified"},
        ],
    },
)

_BRANCH_3169 = "feat/issue-3169-2309b9d6"

PR_LIST_OWNERLESS_ALL_OPEN_JSON = json.dumps(
    [
        {"number": 3215, "head": {"ref": "feat/other-a"}},
        {"number": 3214, "head": {"ref": _BRANCH_3169}},
    ],
)

_ISSUE_BODY_WITH_ALLOW = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "allow_paths:\n"
    "  - src/**\n"
    "  - tests/**\n"
    "```\n"
)

_ISSUE_BODY_SRC_ONLY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "allow_paths:\n"
    "  - src/a.py\n"
    "```\n"
)

_PR_DETAIL_WITH_TEST = {
    "number": 3180,
    "files": [
        {"filename": "src/a.py", "status": "modified"},
        {"filename": "tests/test_foo.py", "status": "modified"},
    ],
}

_P1_RESULT_WITH_DERIVED = (
    "implementation log\n"
    "derived_allow_paths:\n"
    "  - tests/test_foo.py\n"
    "PIPELINE_STATUS: IMPL_DONE\n"
)


def test_pr_diff_lines_success_uses_real_list_and_pr_get() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_SUCCESS_JSON)
    client.pr_get.return_value = json.loads(PR_GET_IN_SCOPE_JSON)
    lines = pr_diff_lines(client, "sumipan/nexus", "feat/issue-3172-32b12432-diary")
    assert lines == 5
    client.pr_get.assert_called_once_with(3180, repo="sumipan/nexus")


def test_pr_diff_lines_absent_uses_empty_list() -> None:
    client = MagicMock()
    client.api_request.return_value = []
    lines = pr_diff_lines(client, "sumipan/nexus", "feat/nonexistent")
    assert lines == 9999
    client.pr_get.assert_not_called()


def test_unchecked_ac_count_only_inside_section() -> None:
    from issuesmith.config import get_config

    ac_heading = get_config().sections["acceptance_criteria"]
    body = (
        "## c6982_c8981\n- [ ] ignore\n"
        f"## {ac_heading}\n- [x] done\n- [ ] todo\n- [ ] other\n"
        "## Out of Scope\n- [ ] ignore2\n"
    )
    assert unchecked_ac_count(body) == 2


def test_find_pr_for_branch_matches_head_ref_not_first_listed() -> None:
    client = MagicMock()
    client.api_request.return_value = json.loads(PR_LIST_OWNERLESS_ALL_OPEN_JSON)
    client.pr_get.return_value = {"number": 3214, "additions": 1, "deletions": 0}
    number, detail = find_pr_for_branch(client, "sumipan/nexus", _BRANCH_3169)
    assert number == 3214
    assert detail is not None


def test_find_pr_for_branch_body_refs_fallback() -> None:
    client = MagicMock()
    client.api_request.side_effect = [
        [],
        [{"number": 4001, "body": "Refs #3162\n", "head": {"ref": "other-branch"}}],
    ]
    number, _detail = find_pr_for_branch(
        client, "sumipan/nexus", "feat/missing", issue_number=3162
    )
    assert number == 4001


def test_find_pr_for_branch_issue_body_link_fallback() -> None:
    client = MagicMock()
    client.api_request.return_value = [
        {"number": 4002, "body": "", "head": {"ref": "other-branch"}},
    ]
    issue_body = "See PR #4002 for the implementation."
    number, _detail = find_pr_for_branch(
        client,
        "sumipan/nexus",
        "feat/missing",
        issue_body=issue_body,
    )
    assert number == 4002


def test_derived_allow_paths_from_result_parses_block(tmp_path: Path) -> None:
    path = tmp_path / "p1.md"
    path.write_text(
        "x\nderived_allow_paths:\n  - tests/a.py\n  - tests/b.py\nPIPELINE_STATUS: IMPL_DONE\n",
        encoding="utf-8",
    )
    assert derived_allow_paths_from_result(path) == ["tests/a.py", "tests/b.py"]


def test_derived_allow_paths_from_result_missing_file_returns_empty(tmp_path: Path) -> None:
    assert derived_allow_paths_from_result(tmp_path / "missing.md") == []


def test_derived_allow_paths_from_result_missing_block_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "p1.md"
    path.write_text("PIPELINE_STATUS: IMPL_DONE\n", encoding="utf-8")
    assert derived_allow_paths_from_result(path) == []


def test_derived_allow_paths_from_result_concatenated_line(tmp_path: Path) -> None:
    path = tmp_path / "p1.md"
    path.write_text(
        "PIPELINE_STATUS: REPAIR_DONEderived_allow_paths:\n"
        "  - tests/test_foo.py\n",
        encoding="utf-8",
    )
    assert derived_allow_paths_from_result(path) == ["tests/test_foo.py"]


def test_derived_allow_paths_from_result_invalid_entry_raises(tmp_path: Path) -> None:
    path = tmp_path / "p1.md"
    path.write_text("derived_allow_paths:\n  - /etc/passwd\n", encoding="utf-8")
    with pytest.raises(DerivedAllowPathsError):
        derived_allow_paths_from_result(path)


def test_derived_allow_paths_from_result_rejects_non_path_type() -> None:
    with pytest.raises(TypeError):
        derived_allow_paths_from_result(123)  # type: ignore[arg-type]


def test_check_pr_scope_with_derived_allows_p1_result(tmp_path: Path) -> None:
    path = tmp_path / "p1.md"
    path.write_text(_P1_RESULT_WITH_DERIVED, encoding="utf-8")
    violations = check_pr_scope_with_derived(
        _PR_DETAIL_WITH_TEST,
        allow_paths_from_issue_body(_ISSUE_BODY_SRC_ONLY),
        result_path=path,
    )
    assert violations == []


def test_check_pr_scope_with_derived_fails_without_result() -> None:
    violations = check_pr_scope_with_derived(
        _PR_DETAIL_WITH_TEST,
        allow_paths_from_issue_body(_ISSUE_BODY_SRC_ONLY),
    )
    assert len(violations) == 1
    assert "tests/test_foo.py" in (violations[0].location or "")


def test_check_pr_scope_with_derived_reports_forbidden_path() -> None:
    detail = json.loads(PR_GET_FORBIDDEN_JSON)
    violations = check_pr_scope_with_derived(
        detail,
        allow_paths_from_issue_body(_ISSUE_BODY_WITH_ALLOW),
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.forbidden_path"
    assert "jobs/exec.jsonl" in (violations[0].location or "")



# --- #4813: allow_paths normalization (leading ./ and normpath) ---


def _issue_body_with_allow_paths(*paths: str) -> str:
    lines = "".join(f"  - {p!r}\n" for p in paths)
    return f"```yaml\ntarget_repo: sumipan/issuesmith\nallow_paths:\n{lines}```\n"


def test_normalize_allow_path_strips_leading_dot_slash() -> None:
    assert normalize_allow_path("./CHANGELOG.md") == "CHANGELOG.md"
    assert normalize_allow_path("./docs/*.md") == "docs/*.md"
    assert normalize_allow_path("src//pkg/./mod.py") == "src/pkg/mod.py"
    assert normalize_allow_path("src/**") == "src/**"


@pytest.mark.parametrize("bad", ["", " ", ".", "./", "/etc/passwd", "../README.md", "src/../../x"])
def test_normalize_allow_path_rejects_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_allow_path(bad)


def test_allow_paths_from_issue_body_normalizes_dot_slash() -> None:
    body = _issue_body_with_allow_paths("./CHANGELOG.md", "src/**")
    assert allow_paths_from_issue_body(body) == ["CHANGELOG.md", "src/**"]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../README.md", "."])
def test_allow_paths_from_issue_body_rejects_invalid(bad: str) -> None:
    body = _issue_body_with_allow_paths("src/**", bad)
    with pytest.raises(ValueError):
        allow_paths_from_issue_body(body)


def test_check_pr_diff_scope_dot_slash_allow_path_matches() -> None:
    violations = check_pr_diff_scope(["CHANGELOG.md"], ["./CHANGELOG.md"], [])
    assert not any(v.rule_id == "pr_diff_scope.out_of_scope" for v in violations)


def test_check_pr_diff_scope_dot_slash_glob_matches() -> None:
    assert check_pr_diff_scope(["docs/a.md"], ["./docs/*.md"], []) == []


def test_check_pr_diff_scope_dot_slash_filename_keeps_original_location() -> None:
    assert check_pr_diff_scope(["./src/a.py"], ["src/**"], []) == []
    violations = check_pr_diff_scope(["./docs/b.md"], ["src/**"], [])
    assert len(violations) == 1
    assert violations[0].location == "./docs/b.md"
    assert "./docs/b.md" in violations[0].message


def test_check_pr_diff_scope_normalized_forbidden_still_detected() -> None:
    violations = check_pr_diff_scope(
        ["./jobs/exec.jsonl"], ["./jobs/**"], list(DEFAULT_FORBIDDEN_PR_PATHS)
    )
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.forbidden_path"


def test_publish_only_exceptions_kept_with_dot_slash_allow_paths() -> None:
    entries = [_FILE_PYPROJECT_VERSION_ONLY, _FILE_CHANGELOG_APPEND_ONLY]
    violations = check_pr_diff_scope(
        _filenames(*entries),
        allow_paths=["./src/**", "./tests/**"],
        forbidden_patterns=list(DEFAULT_FORBIDDEN_PR_PATHS),
        file_entries=entries,
    )
    assert violations == []


def test_derived_allow_paths_from_result_normalizes_dot_slash(tmp_path: Path) -> None:
    path = tmp_path / "p1.md"
    path.write_text("derived_allow_paths:\n  - ./tests/test_foo.py\n", encoding="utf-8")
    assert derived_allow_paths_from_result(path) == ["tests/test_foo.py"]


@pytest.mark.parametrize("bad", [".", "../x.py", "tests/../../x.py"])
def test_derived_allow_paths_from_result_rejects_like_allow_paths(
    tmp_path: Path, bad: str
) -> None:
    path = tmp_path / "p1.md"
    path.write_text(f"derived_allow_paths:\n  - {bad}\n", encoding="utf-8")
    with pytest.raises(DerivedAllowPathsError):
        derived_allow_paths_from_result(path)
