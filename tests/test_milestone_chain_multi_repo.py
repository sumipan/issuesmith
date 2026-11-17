"""Multi-repo milestone chain validation (V1 target_repo / V2 allow_paths filter)."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.language import EN
from issuesmith.milestone import _sub1_build_dep_section, validate_children
from tests.legacy_text import CHANGE_TYPE, DESCRIPTION, FILE_PATH, MODIFY, REPOSITORY

# Split-plan / section vocabulary comes from the language pack (EN in tests); the
# sub-header prefix and change-table columns still use the legacy test pack (conftest).
_, TITLE, TARGET_REPOSITORY, CONTENT, DEPENDENCY = EN.sub_plan_columns
NONE = EN.no_deps_word


def _yaml_block(target_repo: str, allow_paths: list[str]) -> str:
    paths = "\n".join(f"  - {p}" for p in allow_paths)
    return f"""\
```yaml
target_repo: {target_repo}
base_branch: main
allow_paths:
{paths}
```
"""


def _change_table(rows: list[tuple[str, str]]) -> str:
    # ASCII fixture data.
    lines = [
        "**Changed Files**:",
        f"| {REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |",
        "|---|---|---|---|",
    ]
    for repo, path in rows:
        lines.append(f"| `{repo}` | `{path}` | {MODIFY} | change |")
    return "\n".join(lines)


def _parent_with_plan(
    *,
    plan_rows: list[tuple[str, str]],
    parent_repo: str = "sumipan/nexus",
    with_repo_column: bool = True,
) -> str:
    # ASCII fixture data.
    if with_repo_column:
        header = f"| # | {TITLE} | {CONTENT} | {DEPENDENCY} | {TARGET_REPOSITORY} |"
        sep = "|---|--------|------|------|----------------|"
        body_rows = [
            f"| {i} | {title} | scope | {NONE} | `{repo}` |"
            for i, (title, repo) in enumerate(plan_rows, start=1)
        ]
    else:
        header = f"| # | {TITLE} | {CONTENT} | {DEPENDENCY} |"
        sep = "|---|--------|------|------|"
        body_rows = [
            f"| {i} | {title} | scope | {NONE} |"
            for i, (title, _repo) in enumerate(plan_rows, start=1)
        ]
    plan = "\n".join(["### Sub-issue Plan", header, sep, *body_rows])
    return (
        _yaml_block(parent_repo, ["src/**", "companion/**"])
        + "\n\n## Milestone\n\n"
        + plan
        + "\n"
    )


def _child(
    *,
    number: int,
    title: str,
    target_repo: str,
    allow_paths: list[str],
    change_rows: list[tuple[str, str]],
) -> dict:
    body = _yaml_block(target_repo, allow_paths) + "\n" + _change_table(change_rows) + "\n"
    return {
        "number": number,
        "title": title,
        "body": body,
        "milestone": {"number": 1},
        "labels": [{"name": "issuesmith:draft-done"}],
    }


class FakeClient:
    def __init__(self, issues=None):
        self.issues = issues or {}

    def issue_get(self, number, fields=None):
        if number not in self.issues:
            raise RuntimeError(f"missing issue #{number}")
        return dict(self.issues[number])


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def _config_owner_a(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "owner-a/repo-a"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.mark.parametrize(
    ("dep_target_repo", "expect_note"),
    [
        ("owner-a/repo-b", True),
        ("owner-a/repo-a", False),
    ],
)
def test_cross_repo_note_follows_config_repo(
    _config_owner_a, dep_target_repo, expect_note,
):
    """Refs #7156."""
    note = EN.messages["milestone.child_cross_repo_note"]

    class Client:
        def issue_get(self, number, fields=None):
            return {
                "title": "dep",
                "state": "OPEN",
                "body": f"```yaml\ntarget_repo: {dep_target_repo}\n```\n",
            }

    out = _sub1_build_dep_section("#99", row_repo="owner-a/repo-a", client=Client())
    assert (note in out) is expect_note


class TestValidateChildrenMultiRepo:


    def test_v1_fallback_to_parent_repo_without_plan_column(self):
        parent = {
            "number": 100,
            "body": _parent_with_plan(
                plan_rows=[("child one", "sumipan/nexus-companion")],
                parent_repo="sumipan/nexus",
                with_repo_column=False,
            ),
            "milestone": {"number": 1},
            "labels": [],
        }
        matching = _child(
            number=101,
            title="child one",
            target_repo="sumipan/nexus",
            allow_paths=["src/**"],
            change_rows=[("sumipan/nexus", "src/a.py")],
        )
        mismatch = _child(
            number=102,
            title="child one",
            target_repo="sumipan/nexus-companion",
            allow_paths=["companion/**"],
            change_rows=[("sumipan/nexus-companion", "companion/b.py")],
        )
        client = FakeClient(issues={101: matching, 102: mismatch})
        assert validate_children(parent, [matching], client=client).passed is True
        bad = validate_children(parent, [mismatch], client=client)
        assert bad.passed is False
        assert "V1" in bad.results[0].failures[0]


    def test_v2_fail_when_own_repo_path_missing_from_allow_paths(self):
        parent = {
            "number": 100,
            "body": _parent_with_plan(
                plan_rows=[("companion work", "sumipan/nexus-companion")]
            ),
            "milestone": {"number": 1},
            "labels": [],
        }
        child = _child(
            number=101,
            title="companion work",
            target_repo="sumipan/nexus-companion",
            allow_paths=["companion/other/**"],
            change_rows=[
                ("sumipan/nexus", "src/ignored.py"),
                ("sumipan/nexus-companion", "companion/b.py"),
            ],
        )
        client = FakeClient(issues={101: child})
        result = validate_children(parent, [child], client=client)
        assert result.passed is False
        assert any("V2" in f and "companion/b.py" in f for f in result.results[0].failures)
        assert not any("src/ignored.py" in f for f in result.results[0].failures)
