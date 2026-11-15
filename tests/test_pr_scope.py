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


def _filenames(*files: dict) -> list[str]:
    return [f["filename"] for f in files]


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


# --- #4987: forbidden_pr_paths_except ---

_LEDGER_JSONL = "skills/research-store/ledger/a/b.jsonl"
_LEDGER_EXCEPT = ["skills/research-store/ledger/*/*.jsonl"]


def test_forbidden_except_does_not_affect_other_paths() -> None:
    violations = check_pr_diff_scope(
        ["jobs/x.jsonl"],
        ["jobs/**"],
        list(DEFAULT_FORBIDDEN_PR_PATHS),
        forbidden_except=_LEDGER_EXCEPT,
    )
    assert [v.rule_id for v in violations] == ["pr_diff_scope.forbidden_path"]


def test_forbidden_except_still_checks_allow_paths() -> None:
    violations = check_pr_diff_scope(
        [_LEDGER_JSONL],
        ["src/**"],
        list(DEFAULT_FORBIDDEN_PR_PATHS),
        forbidden_except=_LEDGER_EXCEPT,
    )
    assert [v.rule_id for v in violations] == ["pr_diff_scope.out_of_scope"]


