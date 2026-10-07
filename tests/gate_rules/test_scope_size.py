"""Unit tests for ScopeSizeRules (B1 Issue size gate, nexus #3665)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules.scope_size import ScopeSizeRules, measure_size

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_ALL_RULES = {
    "scope_size.too_many_files",
    "scope_size.too_many_concerns",
    "scope_size.delete_with_new",
}

# Change-table header / change types, CJK encoded as cXXXX_ (tests/test_no_cjk.py).
_HEADER = (
    "| c30EA_c30DD_c30B8_c30C8_c30EA_ | c30D5_c30A1_c30A4_c30EB_c30D1_c30B9_ "
    "| c5909_c66F4_c7A2E_c5225_ | c5909_c66F4_c5185_c5BB9_ |\n|---|---|---|---|\n"
)
_MODIFY = "c4FEE_c6B63_"
_NEW = "c65B0_c898F_"
_DELETE = "c524A_c9664_"


def _decode(text: str) -> str:
    return re.sub(r"c([0-9A-F]{4})_?", lambda m: chr(int(m.group(1), 16)), text)


def _fixture(name: str) -> str:
    return _decode((_FIXTURES / name).read_text(encoding="utf-8"))


def _body(rows: list[tuple[str, str]]) -> str:
    table = "".join(f"| `sumipan/issuesmith` | `{p}` | {k} | x |\n" for p, k in rows)
    return _decode(
        "```yaml\ntarget_repo: sumipan/issuesmith\nbase_branch: main\n```\n\n"
        "## Changed Files\n\n" + _HEADER + table
    )


def _vocabulary() -> dict:
    """scope_size vocabulary matching the fixtures' change types (host language)."""
    return {
        "delete_words": [_decode(_DELETE), "delete"],
        "new_words": [_decode(_NEW), "new", "add"],
    }


def _write_config(tmp_path: Path, monkeypatch, extra: dict | None = None) -> None:
    data: dict = {
        "repo": "sumipan/issuesmith",
        "sections": {"changed_files": "Changed Files"},
        "scope_size": _vocabulary(),
    }
    for key, value in (extra or {}).items():
        if key == "scope_size":
            data["scope_size"].update(value)
        else:
            data[key] = value
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


@pytest.fixture(autouse=True)
def _config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch)
    yield
    reset_config_cache()


# Promoted sub change-table rows keep the parent change-content cell (#4825),
# so they are found by their repository cell rather than a placeholder text.
_SUB_ROW_PREFIX = "| `sumipan/issuesmith` |"


def _split_rows(promoted: str) -> list[str]:
    """Change-table rows of the promoted sub blocks (parent table excluded)."""
    from issuesmith.contract import iter_sub_blocks

    return [
        line
        for _, block in iter_sub_blocks(promoted)
        for line in block.splitlines()
        if line.startswith(_SUB_ROW_PREFIX)
    ]


def _ids(violations) -> set[str]:
    return {v.rule_id for v in violations}


# AC-1


def test_original_fixture_fails_all_three_rules():
    violations = ScopeSizeRules().check(_fixture("issue_3627_original.md"), [])
    assert _ids(violations) == _ALL_RULES
    assert len(violations) == 3
    for v in violations:
        assert v.severity == "fail"
        assert v.auto_fixable is False
        assert "tools/issuesmith_steps" in v.fix_hint
        assert "sumipan/nexus" in v.fix_hint


def test_original_fixture_messages():
    by_id = {v.rule_id: v for v in ScopeSizeRules().check(_fixture("issue_3627_original.md"), [])}
    assert "files=10/8" in by_id["scope_size.too_many_files"].message
    concerns = by_id["scope_size.too_many_concerns"].message
    assert "concerns=3/2" in concerns
    assert "tools/issuesmith_steps" in concerns
    mixed = by_id["scope_size.delete_with_new"].message
    assert "workflows/issuesmith/cp1-gate.md" in mixed
    assert "workflows/issuesmith/repair.md" in mixed


def test_original_fixture_measure():
    measure = measure_size(_fixture("issue_3627_original.md"))
    assert len(measure.files) == 10
    assert list(measure.concerns) == [
        "workflows/issuesmith",
        "tools/issuesmith_steps",
        "workflows",
    ]
    assert measure.kinds == frozenset({"delete", "new", "modify"})


def test_reduced_fixture_passes():
    assert ScopeSizeRules().check(_fixture("issue_3627_reduced.md"), []) == []


# AC-2


def test_milestone_label_skips_evaluation():
    body = _fixture("issue_3627_original.md")
    assert ScopeSizeRules().check(body, ["scope:milestone"]) == []


# AC-3


def test_boundary_eight_files_two_concerns_passes():
    rows = [(f"src/a/f{i}.py", _MODIFY) for i in range(4)]
    rows += [(f"src/b/f{i}.py", _MODIFY) for i in range(4)]
    assert ScopeSizeRules().check(_body(rows), []) == []


def test_nine_files_only_too_many_files():
    rows = [(f"src/a/f{i}.py", _MODIFY) for i in range(5)]
    rows += [(f"src/b/f{i}.py", _MODIFY) for i in range(4)]
    assert _ids(ScopeSizeRules().check(_body(rows), [])) == {"scope_size.too_many_files"}


def test_three_concerns_only_too_many_concerns():
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    assert _ids(ScopeSizeRules().check(_body(rows), [])) == {"scope_size.too_many_concerns"}


def test_duplicate_rows_are_counted_once():
    rows = [(f"src/a/f{i}.py", _MODIFY) for i in range(8)] + [("src/a/f0.py", _MODIFY)]
    assert ScopeSizeRules().check(_body(rows), []) == []


# AC-4


def test_excluded_paths_are_not_counted():
    rows = [
        ("src/a/x.py", _DELETE),
        ("tests/test_x.py", _NEW),
        ("docs/x.md", _NEW),
        ("README.md", _MODIFY),
        ("CHANGELOG.md", _MODIFY),
        ("pyproject.toml", _MODIFY),
    ]
    rows += [(f"tests/t{i}.py", _MODIFY) for i in range(10)]
    body = _body(rows)
    assert ScopeSizeRules().check(body, []) == []
    measure = measure_size(body)
    assert measure.files == ("src/a/x.py",)
    assert measure.kinds == frozenset({"delete"})


def test_delete_with_new_detected_outside_excludes():
    rows = [("src/a/old.py", _DELETE), ("src/a/new.py", _NEW)]
    assert _ids(ScopeSizeRules().check(_body(rows), [])) == {"scope_size.delete_with_new"}


def test_english_change_types_are_normalized():
    rows = [("src/a/x.py", "Delete"), ("src/a/y.py", "Add"), ("src/a/z.py", "update")]
    measure = measure_size(_body(rows))
    assert measure.kinds == frozenset({"delete", "new", "modify"})


def test_body_without_change_table_passes():
    body = "```yaml\ntarget_repo: sumipan/issuesmith\n```\n\n## Overview\nno table\n"
    assert ScopeSizeRules().check(body, []) == []


# AC-5


def test_disabled_config_skips_evaluation(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"scope_size": {"enabled": False}})
    assert ScopeSizeRules().check(_fixture("issue_3627_original.md"), []) == []


def test_default_vocabulary_is_english(tmp_path, monkeypatch):
    """Without configured words only the English defaults are recognized."""
    _write_config(tmp_path, monkeypatch, {"scope_size": {"delete_words": ["delete"], "new_words": ["new", "add"]}})
    host_rows = [("src/a/x.py", _DELETE), ("src/a/y.py", _NEW)]
    assert measure_size(_body(host_rows)).kinds == frozenset({"modify"})
    english_rows = [("src/a/x.py", "Delete"), ("src/a/y.py", "Added")]
    assert measure_size(_body(english_rows)).kinds == frozenset({"delete", "new"})


def test_fix_hint_uses_configured_sub_plan_vocabulary(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"scope_size": {"sub_plan_header": "| # | T | R | C | D |", "no_deps_word": "-"}},
    )
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    (violation,) = ScopeSizeRules().check(_body(rows), [])
    assert "| # | T | R | C | D |" in violation.fix_hint
    assert "| 1 | src/a | sumipan/issuesmith | src/a/x.py | - |" in violation.fix_hint


def test_fix_hint_default_vocabulary_is_ascii():
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    (violation,) = ScopeSizeRules().check(_body(rows), [])
    assert "| # | Title | Target repo | Content | Depends on |" in violation.fix_hint
    assert violation.fix_hint.endswith("| none |")


def test_fix_hint_requires_sub_design_blocks():
    from issuesmith.config import get_config
    from tests.legacy_text import SUB

    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    (violation,) = ScopeSizeRules().check(_body(rows), [])
    assert get_config().sections["design"] in violation.fix_hint
    assert "b1_milestone_subdesign" in violation.fix_hint
    assert "apply_deterministic_recovery" in violation.fix_hint
    assert f"#### {SUB}N:" in violation.fix_hint
    assert "#### Sub N:" not in violation.fix_hint
    for name in get_config().sub_design_subsections:
        assert name in violation.fix_hint


def test_promote_oversized_issue_body_writes_japanese_sub_headers():
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body
    from tests.legacy_text import SUB

    rows = [(f"src/a/f{i}.py", _MODIFY) for i in range(4)]
    rows += [(f"src/b/f{i}.py", _MODIFY) for i in range(4)]
    rows += [(f"src/c/f{i}.py", _MODIFY) for i in range(2)]
    body = _body(rows)
    promoted = promote_oversized_issue_body(body)
    assert f"#### {SUB}1:" in promoted
    assert "#### Sub " not in promoted
    # Idempotent
    assert promote_oversized_issue_body(promoted) == promoted


def test_sibling_skill_dirs_aggregate_to_grandparent_concern():
    rows = [
        (f".agents/skills/source-command-{name}/SKILL.md", _DELETE)
        for name in (
            "bookmark",
            "diary",
            "github",
            "morning",
            "news",
            "statusline",
            "voice",
        )
    ]
    rows.append(("configs/settings.json", _MODIFY))
    measure = measure_size(_body(rows))
    assert len(measure.files) == 8
    assert list(measure.concerns) == [".agents/skills", "configs"]
    assert len(measure.concerns[".agents/skills"]) == 7
    assert ScopeSizeRules().check(_body(rows), []) == []


def test_promote_keeps_root_and_excluded_rows():
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    rows = [(f"src/{d}/x.py", _MODIFY) for d in ("a", "b", "c")]
    rows += [("CHANGELOG.md", _MODIFY), ("tests/test_x.py", _NEW)]
    promoted = promote_oversized_issue_body(_body(rows))
    split_rows = _split_rows(promoted)
    for path, _ in rows:
        assert any(f"`{path}`" in line for line in split_rows), path


def test_root_level_files_are_not_concerns():
    rows = [
        ("CHANGELOG.md", _MODIFY),
        ("pyproject.toml", _MODIFY),
        ("src/a/x.py", _MODIFY),
    ]
    measure = measure_size(_body(rows))
    assert list(measure.concerns) == ["src/a"]


def test_config_thresholds_are_honoured(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"scope_size": {"max_files": 20, "max_concerns": 4, "delete_with_new": True}},
    )
    assert ScopeSizeRules().check(_fixture("issue_3627_original.md"), []) == []


# --- Language pack vocabulary (nexus #4474) ---

# ASCII vocabulary that differs from the EN pack defaults.
_ASCII_PACK = {
    "sub_header_prefix": "Part",
    "change_table_columns": ["Repo", "Path", "Kind", "Note"],
    "delete_words": ["drop"],
    "new_words": ["create"],
}


def _use_pack(tmp_path: Path, monkeypatch, **overrides) -> None:
    """Point the config at a language pack YAML: the EN pack with ``overrides``."""
    import dataclasses

    from issuesmith.language import EN

    data = {f.name: getattr(EN, f.name) for f in dataclasses.fields(EN)}
    data.update(overrides)
    plain = {k: list(v) if isinstance(v, tuple) else v for k, v in data.items()}
    plain["sections"] = dict(EN.sections)
    plain["messages"] = dict(EN.messages)
    pack_path = tmp_path / "language_pack.yaml"
    pack_path.write_text(yaml.safe_dump(plain), encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith", "language_pack": str(pack_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _pack_body(columns, rows: list[tuple[str, str]]) -> str:
    table = "".join(f"| `sumipan/issuesmith` | `{p}` | {k} | x |\n" for p, k in rows)
    return (
        "```yaml\ntarget_repo: sumipan/issuesmith\nbase_branch: main\n```\n\n"
        "## Changed Files\n\n| " + " | ".join(columns) + " |\n|---|---|---|---|\n" + table
    )


_OVERSIZED_ROWS = [(f"src/{d}/f{i}.py", "modify") for d in ("a", "b", "c") for i in range(3)]


@pytest.mark.parametrize(
    "rows",
    [
        _OVERSIZED_ROWS,
        [("src/a/x.py", "delete"), ("src/a/y.py", "new")],
        [("src/a/x.py", "modify")],
    ],
    ids=["oversized", "delete_with_new", "small"],
)
def test_ascii_pack_matches_en_decision(tmp_path, monkeypatch, rows):
    from issuesmith.language import EN

    ascii_kinds = {"delete": "drop", "new": "create", "modify": "modify"}
    en_dir = tmp_path / "en"
    en_dir.mkdir()
    _use_pack(en_dir, monkeypatch)
    en_ids = _ids(ScopeSizeRules().check(_pack_body(EN.change_table_columns, rows), []))

    ascii_dir = tmp_path / "ascii"
    ascii_dir.mkdir()
    _use_pack(ascii_dir, monkeypatch, **_ASCII_PACK)
    ascii_rows = [(p, ascii_kinds[k]) for p, k in rows]
    ascii_ids = _ids(
        ScopeSizeRules().check(_pack_body(_ASCII_PACK["change_table_columns"], ascii_rows), [])
    )
    assert ascii_ids == en_ids
    if len(rows) == 2:
        assert "scope_size.delete_with_new" in en_ids


def test_promote_writes_pack_sub_header_and_columns(tmp_path, monkeypatch):
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    _use_pack(tmp_path, monkeypatch, **_ASCII_PACK)
    body = _pack_body(_ASCII_PACK["change_table_columns"], _OVERSIZED_ROWS)
    violations = ScopeSizeRules().check(body, [])
    assert "scope_size.too_many_files" in _ids(violations)
    assert all("#### PartN:" in (v.fix_hint or "") for v in violations)
    promoted = promote_oversized_issue_body(body)
    assert "#### Part1:" in promoted
    assert "| Repo | Path | Kind | Note |" in promoted


def _sub_header_concerns(promoted: str) -> list[str]:
    return re.findall(r"^#### \S+?\d+: (.+)$", promoted, re.MULTILINE)


def _plan_concerns(promoted: str) -> list[str]:
    return re.findall(r"^\| \d+ \| ([^|]+?) \|", promoted, re.MULTILINE)


@pytest.fixture
def _root_readable_extractor(monkeypatch):
    """Simulate nexus #4797 sub 1: change_paths_for_repo keeps root-level files.

    Root concern naming is only observable once root sub blocks are readable;
    until then the #4852 producer gate merges them into the first concern.
    """
    from issuesmith.contract import extract_change_table_rows
    from issuesmith.gate_rules import scope_size

    def _extract(text: str, repo: str | None = None) -> list[str]:
        paths: list[str] = []
        for row_repo, path, _ in extract_change_table_rows(text):
            if repo is not None and row_repo and row_repo != repo:
                continue
            if path not in paths:
                paths.append(path)
        return paths

    monkeypatch.setattr(scope_size, "change_paths_for_repo", _extract)


@pytest.mark.usefixtures("_root_readable_extractor")
def test_promote_names_root_level_concern_root():
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    rows = [(f"src/{d}/x.py", _MODIFY) for d in ("a", "b", "c")]
    rows += [("README.md", _MODIFY), ("CHANGELOG.md", _MODIFY)]
    promoted = promote_oversized_issue_body(_body(rows))
    headers = _sub_header_concerns(promoted)
    assert headers == ["src/a", "src/b", "src/c", "root"]
    assert "." not in headers
    assert _plan_concerns(promoted) == headers
    assert "Concern `root` change table lists every assigned path" in promoted
    assert "Concern `.`" not in promoted
    root_block = promoted.split(": root\n", 1)[1]
    split_rows = [
        line for line in root_block.splitlines() if line.startswith(_SUB_ROW_PREFIX)
    ]
    assert [line.split("`")[3] for line in split_rows] == ["README.md", "CHANGELOG.md"]


@pytest.mark.usefixtures("_root_readable_extractor")
def test_promote_keeps_subdirectory_concern_names():
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    rows = [(f"src/issuesmith/gate_rules/f{i}.py", _MODIFY) for i in range(5)]
    rows += [(f"tests/gate_rules/t{i}.py", _MODIFY) for i in range(3)]
    rows += [("docs/x.md", _MODIFY), ("README.md", _MODIFY)]
    promoted = promote_oversized_issue_body(_body(rows))
    assert _sub_header_concerns(promoted) == [
        "src/issuesmith/gate_rules",
        "tests/gate_rules",
        "docs",
        "root",
    ]
    block = promoted.split(": src/issuesmith/gate_rules\n", 1)[1].split("#### ", 1)[0]
    assert (
        "Concern `src/issuesmith/gate_rules` change table lists every assigned path"
        in block
    )
    for i in range(5):
        assert f"`src/issuesmith/gate_rules/f{i}.py`" in block


def test_grandparent_merge_at_root_is_named_root():
    rows = [(f"skill-{name}/SKILL.md", _MODIFY) for name in ("a", "b", "c")]
    rows += [(f"src/a/f{i}.py", _MODIFY) for i in range(2)]
    rows += [(f"src/b/f{i}.py", _MODIFY) for i in range(2)]
    measure = measure_size(_body(rows))
    # Concern keys (and their file sets) are unchanged; only the display differs.
    assert measure.concerns["."] == tuple(p for p, _ in rows[:3])
    violations = ScopeSizeRules().check(_body(rows), [])
    concern_violation = next(
        v for v in violations if v.rule_id == "scope_size.too_many_concerns"
    )
    assert "(src/a, src/b, root)" in concern_violation.message
    assert "| 3 | root |" in concern_violation.fix_hint


# nexus #4852: producer-side gate — every promoted sub block must be readable by
# the SUB1 / B1 extraction (change_paths_for_repo).


def _promoted_sub_blocks(promoted: str) -> list[tuple[int, str]]:
    from issuesmith.contract import iter_sub_blocks

    return iter_sub_blocks(promoted)


def test_promote_merges_unreadable_root_concern_into_first_readable():
    from issuesmith.contract import change_paths_for_repo
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    rows = [(f"src/{d}/x.py", _MODIFY) for d in ("a", "b", "c")]
    rows += [("issuesmith.yaml", _MODIFY), ("pyproject.toml", _MODIFY)]
    promoted = promote_oversized_issue_body(_body(rows))
    blocks = _promoted_sub_blocks(promoted)
    assert blocks
    for _, block in blocks:
        assert change_paths_for_repo(block, "sumipan/issuesmith"), block
    # Before nexus #4797 sub 1 the extractor drops root-level files, so the
    # root rows must have been merged into the first readable concern.
    if "root" not in _sub_header_concerns(promoted):
        assert _sub_header_concerns(promoted) == ["src/a", "src/b", "src/c"]
        assert _plan_concerns(promoted) == ["src/a", "src/b", "src/c"]
        first = blocks[0][1]
        assert "`issuesmith.yaml`" in first
        assert "`pyproject.toml`" in first
    # File-union complete: no parent row is lost.
    split_rows = _split_rows(promoted)
    for path, _ in rows:
        assert any(f"`{path}`" in line for line in split_rows), path
    # Idempotent after the merge reduced the concern count.
    assert promote_oversized_issue_body(promoted) == promoted


def test_promote_sub_blocks_pass_change_paths_readable_check():
    from issuesmith.gate_rules.b1_milestone_subdesign import B1MilestoneSubdesignRules
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    rows = [(f"src/{d}/x.py", _MODIFY) for d in ("a", "b", "c")]
    rows += [("Procfile", _MODIFY), ("pyproject.toml", _MODIFY)]
    promoted = promote_oversized_issue_body(_body(rows))
    rules = B1MilestoneSubdesignRules()
    for sub_num, block in _promoted_sub_blocks(promoted):
        assert rules._check_change_paths_readable(promoted, sub_num, block) == []


def test_promote_keeps_single_root_concern_when_nothing_is_readable(monkeypatch):
    from issuesmith.gate_rules import scope_size

    monkeypatch.setattr(scope_size, "change_paths_for_repo", lambda text, repo=None: [])
    rows = [(f"src/{d}/x.py", _MODIFY) for d in ("a", "b", "c")]
    rows += [("README.md", _MODIFY)]
    promoted = scope_size.promote_oversized_issue_body(_body(rows))
    assert _sub_header_concerns(promoted) == ["root"]
    split_rows = _split_rows(promoted)
    assert len(split_rows) == len(rows)
    for path, _ in rows:
        assert any(f"`{path}`" in line for line in split_rows), path


def test_promote_without_target_repo_skips_readability_gate(monkeypatch):
    from issuesmith.gate_rules import scope_size

    monkeypatch.setattr(scope_size, "change_paths_for_repo", lambda text, repo=None: [])
    rows = [(f"src/{d}/x.py", _MODIFY) for d in ("a", "b", "c")]
    rows += [("README.md", _MODIFY)]
    body = _body(rows).replace("target_repo: sumipan/issuesmith\n", "")
    promoted = scope_size.promote_oversized_issue_body(body)
    assert _sub_header_concerns(promoted) == ["src/a", "src/b", "src/c", "root"]


# nexus #4825: change content survives promotion and drives the depends-on column.


def _content_body(rows: list[tuple[str, str, str]]) -> str:
    table = "".join(f"| `sumipan/issuesmith` | `{p}` | {k} | {c} |\n" for p, k, c in rows)
    return _decode(
        "```yaml\ntarget_repo: sumipan/issuesmith\nbase_branch: main\n```\n\n"
        "## Changed Files\n\n" + _HEADER + table
    )


def _plan_lines(text: str) -> list[str]:
    return re.findall(r"^\| \d+ \| .+\|$", text, re.MULTILINE)


_ONE_WAY_ROWS = [
    ("src/a/projection.py", _NEW, "new projection module"),
    ("src/a/helpers.py", _MODIFY, "keep helpers"),
    ("src/b/user.py", _MODIFY, "call projection.render"),
    ("src/c/z.py", _MODIFY, "standalone change"),
]

_CYCLE_ROWS = [
    ("src/a/projection.py", _NEW, "new projection module"),
    ("src/a/andon.py", _MODIFY, "call labels.project_issue"),
    ("src/b/labels.py", _NEW, "new label ops"),
    ("src/b/dispatch.py", _MODIFY, "use projection"),
    ("src/c/z.py", _MODIFY, "standalone change"),
]


def test_promote_keeps_change_content_per_sub():
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_content_body(_ONE_WAY_ROWS))
    rows = _split_rows(promoted)
    for path, _, content in _ONE_WAY_ROWS:
        (row,) = [line for line in rows if f"`{path}`" in line]
        assert row.endswith(f"| {content} |")
    assert "split from oversized issue" not in promoted


def test_promote_writes_creator_dependency_and_no_deps_word():
    from issuesmith.config import get_config
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    no_deps = get_config().scope_size.no_deps_word
    promoted = promote_oversized_issue_body(_content_body(_ONE_WAY_ROWS))
    assert _plan_lines(promoted) == [
        f"| 1 | src/a | sumipan/issuesmith | src/a/projection.py, src/a/helpers.py | {no_deps} |",
        "| 2 | src/b | sumipan/issuesmith | src/b/user.py | #1 |",
        f"| 3 | src/c | sumipan/issuesmith | src/c/z.py | {no_deps} |",
    ]
    assert promote_oversized_issue_body(promoted) == promoted


def test_promote_merges_mutually_dependent_concerns():
    from issuesmith.contract import extract_change_table_rows
    from issuesmith.gate_rules.b1_milestone_subdesign import (
        B1MilestoneSubdesignRules,
        find_dependency_cycles,
        infer_sub_dependencies,
    )
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    promoted = promote_oversized_issue_body(_content_body(_CYCLE_ROWS))
    assert _sub_header_concerns(promoted) == ["src/a + src/b", "src/c"]
    blocks = _promoted_sub_blocks(promoted)
    owned = [path for _, block in blocks for _, path, _ in extract_change_table_rows(block)]
    assert sorted(owned) == sorted(path for path, _, _ in _CYCLE_ROWS)
    assert len(owned) == len(set(owned))
    deps = infer_sub_dependencies(blocks)
    assert find_dependency_cycles(deps) == []
    ids = {v.rule_id for v in B1MilestoneSubdesignRules().check(promoted, ["scope:milestone"])}
    assert "b1_milestone_subdesign.dependency_cycle" not in ids
    assert "b1_milestone_subdesign.sibling_new_file_unreferenced_dependency" not in ids


@pytest.mark.parametrize("rows", [_ONE_WAY_ROWS, _CYCLE_ROWS], ids=["one_way", "cycle"])
def test_fix_hint_plan_matches_promotion(rows):
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    body = _content_body(rows)
    violations = ScopeSizeRules().check(body, [])
    assert violations
    promoted_plan = _plan_lines(promote_oversized_issue_body(body))
    for v in violations:
        assert _plan_lines(v.fix_hint) == promoted_plan


def test_promote_dependency_vocabulary_from_pack(tmp_path, monkeypatch):
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    _use_pack(
        tmp_path,
        monkeypatch,
        **_ASCII_PACK,
        sub_plan_columns=["#", "Title", "Repo", "What", "Needs"],
        no_deps_word="-",
    )
    rows = [
        ("src/a/projection.py", "create", "new projection"),
        ("src/b/user.py", "modify", "call projection.render"),
        ("src/c/z.py", "modify", "standalone"),
    ]
    table = "".join(f"| `sumipan/issuesmith` | `{p}` | {k} | {c} |\n" for p, k, c in rows)
    body = (
        "```yaml\ntarget_repo: sumipan/issuesmith\nbase_branch: main\n```\n\n"
        "## Changed Files\n\n| Repo | Path | Kind | Note |\n|---|---|---|---|\n" + table
    )
    promoted = promote_oversized_issue_body(body)
    assert "| # | Title | Repo | What | Needs |" in promoted
    assert [line.rsplit("|", 2)[1].strip() for line in _plan_lines(promoted)] == ["-", "#1", "-"]
