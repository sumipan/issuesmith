"""SUB1 split-plan parsing and child bodies follow the language pack (nexus #4470)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

import issuesmith.config as config_module
from issuesmith.config import reset_config_cache
from issuesmith.language import EN, LanguagePack, load_language_pack
from issuesmith.milestone import (
    _sub1_parent_design_gate,
    build_child_body,
    check_v3_cjk_placeholders,
    parse_split_plan,
    prevalidate_child_body,
    resolve_dependencies,
)

_REPO = "example/app"

# ASCII vocabulary that differs from EN in every field SUB1 reads.
_CUSTOM_OVERRIDES = {
    "sections": {key: f"X {heading}" for key, heading in EN.sections.items()},
    "sub_design_subsections": ["Area", "Approach", "Touched Files", "Done When"],
    "sub_header_prefix": "Part",
    "sub_plan_columns": ["No", "Name", "Repo", "Work", "After"],
    "change_table_columns": ["Repo", "Path", "Kind", "Note"],
    "no_deps_word": "nil",
    "placeholder_words": ["FILLME"],
    "derived_from_phrase": "spun off from",
    "parent_issue_label": "Parent",
    "dependencies_table_header": "| No | Needs | Status |",
}


def _use_pack(monkeypatch, pack: LanguagePack) -> LanguagePack:
    monkeypatch.setattr(config_module, "EN", pack)
    reset_config_cache()
    return pack


def _write_custom_pack(tmp_path: Path) -> Path:
    data: dict = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(EN, f.name)
        data[f.name] = list(value) if isinstance(value, tuple) else (
            value if isinstance(value, str) else dict(value)
        )
    data.update(_CUSTOM_OVERRIDES)
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _parent_body(lang: LanguagePack) -> str:
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

    return (
        f"```yaml\ntarget_repo: {_REPO}\nbase_branch: main\n```\n\n"
        f"## {s['design']}\n\nParent design.\n\n"
        + sub(1, "src/a.py")
        + sub(2, "src/b.py")
        + f"## {s['acceptance_criteria']}\n\n- [ ] all parts land\n\n"
        f"## {s['milestone']}\n\n### {s['sub_plan']}\n\n"
        f"{lang.sub_plan_header}\n|---|---|---|---|---|\n"
        f"| 1 | first part | `{_REPO}` | do a | {lang.no_deps_word} |\n"
        f"| 2 | second part | `{_REPO}` | do b | #1 |\n"
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.issue_get.return_value = {
        "title": "first part",
        "state": "OPEN",
        "body": f"```yaml\ntarget_repo: {_REPO}\n```\n",
        "labels": [],
    }
    return client


def _run_sub1(lang: LanguagePack) -> dict:
    """Parse the plan and build both child bodies the way run_sub1_create does."""
    body = _parent_body(lang)
    assert _sub1_parent_design_gate(body) is None
    rows, has_repo = parse_split_plan(body, parent_target_repo=_REPO)
    client = _client()
    state = MagicMock(resolved_logs=[], unresolved_forward_logs=[], excluded_milestone_logs=[])
    row_to_issue: dict[int, int] = {}
    deps: list[str] = []
    children: list[str] = []
    for row in rows:
        dep = resolve_dependencies(
            row.dep_raw,
            table_row_count=len(rows),
            row_to_issue=row_to_issue,
            client=client,
            state=state,
        )
        child = build_child_body(
            parent_body=body,
            parent_number=4470,
            row=row,
            resolved_dep=dep,
            client=client,
            parent_labels=["scope:milestone"],
        )
        failures = prevalidate_child_body(
            body=child,
            row_repo=row.repo,
            parent_issue_number=4470,
            resolved_dep=dep,
            client=client,
            supported={_REPO},
        )
        assert failures == []
        row_to_issue[row.row_num] = 9000 + row.row_num
        deps.append(dep)
        children.append(child)
    return {
        "rows": [(r.row_num, r.title, r.repo, r.scope, r.dep_raw) for r in rows],
        "has_repo": has_repo,
        "deps": deps,
        "children": children,
    }


def _normalize(text: str, lang: LanguagePack) -> str:
    """Replace every pack word with a role token so two packs' outputs compare equal."""
    words: dict[str, str] = {f"<section:{k}>": v for k, v in lang.sections.items()}
    words.update({f"<sub:{i}>": v for i, v in enumerate(lang.sub_design_subsections)})
    # Column 0 ("#" in EN) is skipped: it would clash with Issue refs.
    words.update({f"<plan:{i}>": v for i, v in enumerate(lang.sub_plan_columns) if i})
    words.update(
        {
            "<prefix>": lang.sub_header_prefix,
            "<none>": lang.no_deps_word,
            "<derived>": lang.derived_from_phrase,
            "<parent>": lang.parent_issue_label,
            "<deps-header>": lang.dependencies_table_header,
        }
    )
    for token, word in sorted(words.items(), key=lambda kv: len(kv[1]), reverse=True):
        text = text.replace(word, token)
    return text


def test_en_pack_builds_child_bodies_from_english_plan(monkeypatch):
    lang = _use_pack(monkeypatch, EN)
    assert lang.sub_plan_header == "| # | Title | Target repo | Content | Depends on |"

    result = _run_sub1(lang)

    assert result["rows"] == [
        (1, "first part", _REPO, "do a", "none"),
        (2, "second part", _REPO, "do b", "#1"),
    ]
    assert result["has_repo"] is True
    assert result["deps"] == ["none", "#9001"]
    first, second = result["children"]
    assert "Parent issue: #4470\nDepends on: none\n" in first
    assert "> Parent issue #4470 Sub1 derived" in first
    assert '  - "src/a.py"' in first
    for heading in ("Scope", "Design", "Acceptance Criteria"):
        assert f"\n## {heading}\n" in first
    assert "## Dependencies" not in first
    assert "Depends on: #9001\n" in second
    assert "## Dependencies\n\n| # | Dependency | State |\n|---|---|---|\n" in second
    assert "| 1 | #9001 (first part) | OPEN |" in second
    assert '  - "src/b.py"' in second
    assert all(text.isascii() for text in result["children"])


def test_custom_pack_from_yaml_gives_same_rows_deps_and_structure(tmp_path, monkeypatch):
    expected = _run_sub1(_use_pack(monkeypatch, EN))

    custom = _use_pack(monkeypatch, load_language_pack(_write_custom_pack(tmp_path)))
    actual = _run_sub1(custom)

    assert actual["rows"] == [
        (num, title, repo, scope, "nil" if dep == "none" else dep)
        for num, title, repo, scope, dep in expected["rows"]
    ]
    assert actual["has_repo"] == expected["has_repo"]
    assert actual["deps"] == ["nil", "#9001"]
    assert [_normalize(c, custom) for c in actual["children"]] == [
        _normalize(c, EN) for c in expected["children"]
    ]
    assert "Parent: #4470\nAfter: nil\n" in actual["children"][0]
    assert "## X Dependencies\n\n| No | Needs | Status |\n" in actual["children"][1]


def test_custom_pack_drives_plan_columns_and_design_gate(tmp_path, monkeypatch):
    _use_pack(monkeypatch, load_language_pack(_write_custom_pack(tmp_path)))
    en_body = _parent_body(EN)

    rows, _ = parse_split_plan(en_body, parent_target_repo=_REPO)

    assert rows == []
    gate = _sub1_parent_design_gate(en_body)
    assert gate is not None and "X Design" in gate


def test_placeholder_words_come_from_pack(tmp_path, monkeypatch):
    body = "## Scope\n\nFILLME\n"
    _use_pack(monkeypatch, EN)
    assert check_v3_cjk_placeholders(body="## Scope\n\nTODO\n") == ["V3 CJK placeholder detected"]
    assert check_v3_cjk_placeholders(body=body) == []

    _use_pack(monkeypatch, load_language_pack(_write_custom_pack(tmp_path)))
    assert check_v3_cjk_placeholders(body=body) == ["V3 CJK placeholder detected"]
    assert check_v3_cjk_placeholders(body="## Scope\n\nTODO\n") == []
