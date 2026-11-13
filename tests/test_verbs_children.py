"""Public ``issuesmith.verbs.children`` split-plan orchestration (#6583)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import yaml

import issuesmith.config as config_module
from issuesmith.config import get_config, reset_config_cache
from issuesmith.language import EN
from issuesmith.verbs import children as vchildren

_REPO = "sumipan/issuesmith"


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    from ghdag.forge import get_forge

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "supported_repos": [_REPO, "sumipan/nexus"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    monkeypatch.setattr(config_module, "EN", EN)
    reset_config_cache()
    client = get_forge()
    yield client
    reset_config_cache()


def _parent_body_two_rows(*, duplicate_design: bool = False) -> str:
    lang = EN
    s = lang.sections
    scope, policy, files, ac = lang.sub_design_subsections
    repo_col, path_col, kind_col, note_col = lang.change_table_columns

    def sub(num: int, path: str) -> str:
        return (
            f"#### {lang.sub_header_prefix}{num}: part {num}\n\n"
            f"**{scope}**: work {num}\n\n"
            f"**{policy}**: approach {num}\n\n"
            f"**{files}**:\n"
            f"| {repo_col} | {path_col} | {kind_col} | {note_col} |\n"
            "|---|---|---|---|\n"
            f"| `{_REPO}` | `{path}` | modify | x |\n\n"
            f"**{ac}**:\n- [ ] done {num}\n\n"
        )

    design_blocks = f"## {s['design']}\n\nParent design.\n\n" + sub(1, "src/a.py") + sub(2, "src/b.py")
    if duplicate_design:
        design_blocks += f"\n## {s['design']}\n\nSecond design block.\n"

    return (
        f"```yaml\ntarget_repo: {_REPO}\nbase_branch: main\n```\n\n"
        f"{design_blocks}"
        f"## {s['acceptance_criteria']}\n\n- [ ] all parts land\n\n"
        f"## {s['milestone']}\n\n### {s['sub_plan']}\n\n"
        f"{lang.sub_plan_header}\n|---|---|---|---|---|\n"
        f"| 1 | first part | `{_REPO}` | do a | {lang.no_deps_word} |\n"
        f"| 2 | second part | `{_REPO}` | do b | #1 |\n"
    )


def _create_parent(client, body: str) -> int:
    labels = ["scope:milestone", "issuesmith:draft-done", "issuesmith:sub-ready"]
    return int(client.issue_create("milestone parent", body, labels=labels))


def test_plan_children_resolves_ordered_dependencies(local_client) -> None:
    """Refs sumipan/nexus#6583 — row 2 inherits row 1 issue number in dependencies."""
    parent = _create_parent(local_client, _parent_body_two_rows())
    child1 = local_client.issue_create(
        "first part",
        "```yaml\ntarget_repo: sumipan/issuesmith\n```\n",
        labels=["issuesmith:draft-done"],
    )
    local_client.add_sub_issue(parent, local_client.issue_get(child1, fields=["id"])["id"])

    plan = vchildren.plan_children(local_client, parent, repo=_REPO)
    assert len(plan.entries) == 2
    assert plan.entries[0].existing == child1
    assert plan.entries[1].dependencies == (child1,)


def test_plan_children_rejects_duplicate_design_without_creation(local_client) -> None:
    """Refs sumipan/nexus#6583 — duplicate design sections raise ChildPlanError."""
    parent = _create_parent(local_client, _parent_body_two_rows(duplicate_design=True))
    with pytest.raises(vchildren.ChildPlanError) as exc:
        vchildren.plan_children(local_client, parent, repo=_REPO)
    assert exc.value.violations
    assert local_client.list_sub_issues(parent) == []


def test_create_child_restores_yaml_and_dependencies(local_client) -> None:
    """Refs sumipan/nexus#6583 — polished body keeps draft YAML and dependency block."""
    parent = _create_parent(local_client, _parent_body_two_rows())
    plan = vchildren.plan_children(local_client, parent, repo=_REPO)
    entry = plan.entries[0]
    polished = entry.draft_body.replace(
        "target_repo: sumipan/issuesmith",
        "target_repo: sumipan/evil",
    )
    dep_heading = get_config().sections["dependencies"]
    polished = polished.replace(
        f"## {dep_heading}",
        f"## {dep_heading}\nTampered\n",
        1,
    )

    child_number = vchildren.create_child(local_client, parent, entry, body=polished)
    stored = str(local_client.issue_get(child_number, fields=["body"])["body"])
    assert "target_repo: sumipan/issuesmith" in stored
    assert "target_repo: sumipan/evil" not in stored
    assert stored.count(f"## {dep_heading}") == entry.draft_body.count(f"## {dep_heading}")


def test_create_child_reuses_existing_child(local_client) -> None:
    """Refs sumipan/nexus#6583 — second create returns the same child number."""
    parent = _create_parent(local_client, _parent_body_two_rows())
    plan = vchildren.plan_children(local_client, parent, repo=_REPO)
    entry = plan.entries[0]
    first = vchildren.create_child(local_client, parent, entry)
    replan = vchildren.plan_children(local_client, parent, repo=_REPO)
    existing_entry = next(e for e in replan.entries if e.row_number == entry.row_number)
    assert existing_entry.existing == first
    with patch.object(local_client, "issue_create") as mocked:
        second = vchildren.create_child(local_client, parent, existing_entry)
        mocked.assert_not_called()
    assert first == second


def test_create_child_does_not_write_on_prevalidation_failure(local_client) -> None:
    """Refs sumipan/nexus#6583 — prevalidate failure does not create or link issues."""
    parent = _create_parent(local_client, _parent_body_two_rows())
    plan = vchildren.plan_children(local_client, parent, repo=_REPO)
    bad = vchildren.ChildPlanEntry(
        row_number=plan.entries[0].row_number,
        title=plan.entries[0].title,
        repo="sumipan/unknown",
        existing=None,
        dependencies=plan.entries[0].dependencies,
        allow_paths=plan.entries[0].allow_paths,
        draft_body=plan.entries[0].draft_body,
    )
    with patch.object(local_client, "issue_create") as mocked:
        with pytest.raises(vchildren.ChildPlanError):
            vchildren.create_child(local_client, parent, bad)
        mocked.assert_not_called()
    assert local_client.list_sub_issues(parent) == []


def test_widen_allow_paths_is_idempotent_and_preserves_body(local_client) -> None:
    """Refs sumipan/nexus#6583 — widening paths twice does not duplicate allow_paths."""
    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "allow_paths:\n"
        '  - "src/a.py"\n'
        "```\n\n"
        "## Design\n\nnotes\n"
    )
    number = int(local_client.issue_create("widen", body))
    assert vchildren.widen_allow_paths(local_client, number, ["src/b.py"]) is True
    after_first = str(local_client.issue_get(number, fields=["body"])["body"])
    assert vchildren.widen_allow_paths(local_client, number, ["src/b.py", "src/b.py"]) is True
    after_second = str(local_client.issue_get(number, fields=["body"])["body"])
    assert after_second == after_first
    assert after_first.count("src/b.py") == 1
    assert "## Design\n\nnotes\n" in after_second


def test_widen_allow_paths_returns_false_without_yaml(local_client) -> None:
    """Refs sumipan/nexus#6583 — body without leading YAML is left unchanged."""
    number = int(local_client.issue_create("plain", "## Design\n\nno yaml\n"))
    assert vchildren.widen_allow_paths(local_client, number, ["src/x.py"]) is False
    assert local_client.issue_get(number, fields=["body"])["body"] == "## Design\n\nno yaml\n"
