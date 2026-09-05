"""test_ac_contract_migration.py — migration 契約（removed_trees / post_merge）のユニットテスト。"""
from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.ac_contract import run_checks


def _git_init_with_tree(repo: Path, tree: str, files: dict[str, str]) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", tree], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
        env={
            **dict(__import__("os").environ),
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@test",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@test",
        },
    )


def test_removed_trees_fail_when_tracked_files_remain(tmp_path):
    _git_init_with_tree(
        tmp_path,
        "tools/issuesmith",
        {"tools/issuesmith/foo.py": "x"},
    )
    records = run_checks({"removed_trees": ["tools/issuesmith"]}, tmp_path)
    removed = [r for r in records if r["check"] == "removed_trees"]
    assert len(removed) == 1
    assert removed[0]["result"] == "FAIL"
    assert removed[0]["path"] == "tools/issuesmith"
    assert "files remain" in removed[0]["detail"]


def test_removed_trees_pass_when_tree_empty(tmp_path):
    _git_init_with_tree(tmp_path, "README.md", {"README.md": "ok"})
    records = run_checks({"removed_trees": ["tools/issuesmith"]}, tmp_path)
    removed = [r for r in records if r["check"] == "removed_trees"]
    assert len(removed) == 1
    assert removed[0]["result"] == "PASS"
    assert removed[0]["path"] == "tools/issuesmith"


def test_post_merge_schema_invalid_kind_missing(tmp_path):
    records = run_checks({"post_merge": [{"repo": "sumipan/issuesmith"}]}, tmp_path)
    schema = [r for r in records if r["check"] == "post_merge_schema"]
    assert len(schema) == 1
    assert schema[0]["result"] == "FAIL"
    assert "kind" in schema[0]["detail"]


def test_post_merge_schema_invalid_not_list(tmp_path):
    records = run_checks({"post_merge": "bad"}, tmp_path)
    schema = [r for r in records if r["check"] == "post_merge_schema"]
    assert len(schema) == 1
    assert schema[0]["result"] == "FAIL"


def test_post_merge_schema_valid_list(tmp_path):
    records = run_checks(
        {
            "post_merge": [
                {"kind": "stable_install", "repo": "sumipan/issuesmith", "path": "/var/tmp/issuesmith"},
            ]
        },
        tmp_path,
    )
    schema = [r for r in records if r["check"] == "post_merge_schema"]
    assert schema == [] or all(r["result"] == "PASS" for r in schema)
