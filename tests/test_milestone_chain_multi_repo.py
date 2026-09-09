"""Multi-repo milestone chain validation (V1 target_repo / V2 allow_paths filter)."""

from __future__ import annotations

import pytest

from issuesmith.config import reset_config_cache
from issuesmith.milestone import validate_children


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
    lines = [
        "**変更対象ファイル**:",
        "| リポジトリ | ファイルパス | 変更種別 | 変更内容 |",
        "|---|---|---|---|",
    ]
    for repo, path in rows:
        lines.append(f"| `{repo}` | `{path}` | 修正 | change |")
    return "\n".join(lines)


def _parent_with_plan(
    *,
    plan_rows: list[tuple[str, str]],
    parent_repo: str = "sumipan/nexus",
    with_repo_column: bool = True,
) -> str:
    if with_repo_column:
        header = "| # | タイトル | 内容 | 依存 | 対象リポジトリ |"
        sep = "|---|--------|------|------|----------------|"
        body_rows = [
            f"| {i} | {title} | scope | なし | `{repo}` |"
            for i, (title, repo) in enumerate(plan_rows, start=1)
        ]
    else:
        header = "| # | タイトル | 内容 | 依存 |"
        sep = "|---|--------|------|------|"
        body_rows = [
            f"| {i} | {title} | scope | なし |"
            for i, (title, _repo) in enumerate(plan_rows, start=1)
        ]
    plan = "\n".join(["### サブイシュー分割計画", header, sep, *body_rows])
    return (
        _yaml_block(parent_repo, ["src/**", "companion/**"])
        + "\n\n## マイルストーン\n\n"
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


class TestValidateChildrenMultiRepo:
    def test_v1_pass_when_child_repos_match_plan_column(self):
        parent = {
            "number": 100,
            "body": _parent_with_plan(
                plan_rows=[
                    ("nexus work", "sumipan/nexus"),
                    ("companion work", "sumipan/nexus-companion"),
                ]
            ),
            "milestone": {"number": 1},
            "labels": [{"name": "scope:milestone"}],
        }
        children = [
            _child(
                number=101,
                title="nexus work",
                target_repo="sumipan/nexus",
                allow_paths=["src/**"],
                change_rows=[("sumipan/nexus", "src/a.py")],
            ),
            _child(
                number=102,
                title="companion work",
                target_repo="sumipan/nexus-companion",
                allow_paths=["companion/**"],
                change_rows=[("sumipan/nexus-companion", "companion/b.py")],
            ),
        ]
        client = FakeClient(issues={c["number"]: c for c in children})
        result = validate_children(parent, children, client=client)
        assert result.passed is True

    def test_v1_fail_when_child_repo_mismatches_plan_row(self):
        parent = {
            "number": 100,
            "body": _parent_with_plan(
                plan_rows=[
                    ("nexus work", "sumipan/nexus"),
                    ("companion work", "sumipan/nexus-companion"),
                ]
            ),
            "milestone": {"number": 1},
            "labels": [],
        }
        child = _child(
            number=102,
            title="companion work",
            target_repo="sumipan/nexus",
            allow_paths=["companion/**"],
            change_rows=[("sumipan/nexus-companion", "companion/b.py")],
        )
        client = FakeClient(issues={102: child})
        result = validate_children(parent, [child], client=client)
        assert result.passed is False
        failure = result.results[0].failures[0]
        assert "V1" in failure
        assert "sumipan/nexus-companion" in failure
        assert "sumipan/nexus" in failure

    def test_v1_fail_when_child_repo_not_in_supported_repos(self):
        parent = {
            "number": 100,
            "body": _parent_with_plan(
                plan_rows=[("odd work", "sumipan/not-a-supported-repo")]
            ),
            "milestone": {"number": 1},
            "labels": [],
        }
        child = _child(
            number=101,
            title="odd work",
            target_repo="sumipan/not-a-supported-repo",
            allow_paths=["src/**"],
            change_rows=[("sumipan/not-a-supported-repo", "src/a.py")],
        )
        client = FakeClient(issues={101: child})
        result = validate_children(parent, [child], client=client)
        assert result.passed is False
        joined = " ".join(result.results[0].failures)
        assert "V1" in joined
        assert "supported_repos" in joined
        assert "sumipan/not-a-supported-repo" in joined

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

    def test_v2_ignores_other_repo_paths(self):
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
            allow_paths=["companion/**"],
            change_rows=[
                ("sumipan/nexus", "src/only-in-parent.py"),
                ("sumipan/nexus-companion", "companion/b.py"),
            ],
        )
        client = FakeClient(issues={101: child})
        result = validate_children(parent, [child], client=client)
        assert result.passed is True

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
