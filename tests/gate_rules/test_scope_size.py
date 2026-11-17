"""Unit tests for ScopeSizeRules (B1 Issue size gate, nexus #3665)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from issuesmith.config import get_config, reset_config_cache
from issuesmith.gate_rules.scope_size import ScopeSizeRules, measure_size

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
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


def _body(rows: list[tuple[str, str]], yaml_extra: str = "") -> str:
    table = "".join(f"| `sumipan/issuesmith` | `{p}` | {k} | x |\n" for p, k in rows)
    return _decode(
        "```yaml\ntarget_repo: sumipan/issuesmith\nbase_branch: main\n"
        f"{yaml_extra}"
        "```\n\n"
        "## Changed Files\n\n" + _HEADER + table
    )


def _vocabulary() -> dict:
    """scope_size vocabulary matching the fixtures' change types (host language)."""
    return {
        "delete_words": [_decode(_DELETE), "delete"],
        "new_words": [_decode(_NEW), "new", "add"],
    }


def _write_config(tmp_path: Path, monkeypatch, extra: dict | None = None) -> None:
    import dataclasses

    from issuesmith import config as config_module

    en = config_module.EN
    vocab = _vocabulary()
    pack_data = {f.name: getattr(en, f.name) for f in dataclasses.fields(en)}
    pack_data.update(vocab)
    pack_data["sections"] = dict(en.sections)
    pack_data["messages"] = dict(en.messages)
    pack_path = tmp_path / "language_pack.yaml"
    plain = {
        k: list(v) if isinstance(v, tuple) else v for k, v in pack_data.items()
    }
    pack_path.write_text(yaml.safe_dump(plain), encoding="utf-8")
    data: dict = {
        "repo": "sumipan/issuesmith",
        "language_pack": str(pack_path),
        "scope_size": {"max_concerns": 2},
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




def test_fix_hint_default_vocabulary_is_ascii():
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    (violation,) = ScopeSizeRules().check(_body(rows), [])
    assert "| # | Title | Target repo | Content | Depends on |" in violation.fix_hint
    assert violation.fix_hint.endswith("| none |")








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




def _sub_header_concerns(promoted: str) -> list[str]:
    return re.findall(r"^#### \S+?\d+: (.+)$", promoted, re.MULTILINE)


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



@pytest.mark.parametrize("rows", [_ONE_WAY_ROWS, _CYCLE_ROWS], ids=["one_way", "cycle"])
def test_fix_hint_plan_matches_promotion(rows):
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    body = _content_body(rows)
    violations = ScopeSizeRules().check(body, [])
    assert violations
    promoted_plan = _plan_lines(promote_oversized_issue_body(body))
    for v in violations:
        assert _plan_lines(v.fix_hint) == promoted_plan




def test_promotion_declined_when_no_core_rows():
    from issuesmith.gate_rules.scope_size import (
        promote_oversized_issue_body,
        promotion_declined,
    )

    rows = [(f"configs/c{i}.yaml", _MODIFY) for i in range(9)]
    body = _body(rows)
    assert promotion_declined(body)
    assert promote_oversized_issue_body(body) == body


@pytest.mark.usefixtures("_root_readable_extractor")
def test_corklab_style_promotion_uses_three_slices():
    from issuesmith.contract import extract_change_table_rows
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    step_files = [f"tools/corklab/steps/{name}.py" for name in (
        "b1", "cp1", "cp2", "develop", "drive", "m1", "m2", "merge", "p1", "p2",
    )]
    rows: list[tuple[str, str, str]] = [
        ("tools/corklab/config.py", _MODIFY, "refactor config"),
        ("tools/corklab/labels.py", _DELETE, "remove labels module"),
        ("tools/corklab/steps/merge.py", _MODIFY, "drop labels import"),
    ]
    for path in step_files:
        rows.append((path, _MODIFY, "step change"))
    for name in ("b1.md", "design_lint.md", "develop.md"):
        rows.append((f"workflows/corklab/{name}", _MODIFY, "workflow doc"))
    rows += [
        ("workflows/corklab.yml", _MODIFY, "pipeline"),
        ("configs/corklab.yaml", _MODIFY, "tune config knobs"),
        ("issuesmith-corklab.yaml", _MODIFY, "consumer"),
        ("tests/tools/corklab/test_config.py", _MODIFY, "mirror config"),
        ("tests/tools/corklab/test_steps.py", _MODIFY, "mirror steps"),
        ("tests/workflows/test_corklab_workflow.py", _MODIFY, "workflow test"),
        ("docs/CORKLAB.md", _MODIFY, "doc"),
    ]
    body = _content_body(rows)
    promoted = promote_oversized_issue_body(body)
    headers = _sub_header_concerns(promoted)
    assert headers == ["tools/corklab", "tools/corklab/steps", "workflows/corklab"]
    assert "tests/gate_rules" not in headers
    assert "root" not in headers
    assert "configs" not in headers
    corklab_block = promoted.split(": tools/corklab\n", 1)[1].split("#### ", 1)[0]
    assert "`tools/corklab/labels.py`" in corklab_block
    assert "`tools/corklab/steps/merge.py`" in corklab_block
    assert "`tests/tools/corklab/test_config.py`" in corklab_block
    assert "`configs/corklab.yaml`" in corklab_block
    parent_paths = {path for _, path, _ in extract_change_table_rows(body)}
    promoted_paths = {path for _, path, _ in extract_change_table_rows(promoted)}
    assert parent_paths <= promoted_paths
    assert len(promoted_paths) == len(
        {(r, p) for r, p, _ in extract_change_table_rows(promoted)}
    )



def test_deleted_core_pulls_stem_test_from_other_slice():
    from issuesmith.gate_rules.scope_size import _rows_by_slice, get_config

    rows = [
        ("src/a/x.py", _MODIFY, "a"),
        ("src/a/y.py", _MODIFY, "a"),
        ("src/b/old.py", _DELETE, "gone"),
        ("src/b/z.py", _MODIFY, "b"),
        ("tests/a/test_old.py", _MODIFY, "test"),
    ]
    slices = _rows_by_slice(_content_body(rows), get_config().scope_size)
    paths = {name: [r[1] for r in rs] for name, rs in slices.items()}
    assert "tests/a/test_old.py" in paths["src/b"]
    assert "tests/a/test_old.py" not in paths["src/a"]


def test_fix_hint_includes_split_atomic_guidance():
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    (violation,) = ScopeSizeRules().check(_body(rows), [])
    assert "split: atomic" in violation.fix_hint


def test_three_concerns_pass_with_default_max_concerns(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"scope_size": {"max_concerns": 4}})
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    assert ScopeSizeRules().check(_body(rows), []) == []



def test_atomic_skips_delete_with_new():
    rows = [("src/a/old.py", _DELETE), ("src/a/new.py", _NEW)]
    body = _body(rows, yaml_extra="split: atomic\n")
    assert ScopeSizeRules().check(body, []) == []


def test_split_foo_still_triggers_scope_size():
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    body = _body(rows, yaml_extra="split: foo\n")
    assert _ids(ScopeSizeRules().check(body, [])) == {"scope_size.too_many_concerns"}


def test_body_without_yaml_still_triggers_scope_size():
    rows = [("src/a/x.py", _MODIFY), ("src/b/x.py", _MODIFY), ("src/c/x.py", _MODIFY)]
    table = "".join(f"| `sumipan/issuesmith` | `{p}` | {k} | x |\n" for p, k in rows)
    body = _decode("## Changed Files\n\n" + _HEADER + table)
    assert _ids(ScopeSizeRules().check(body, [])) == {"scope_size.too_many_concerns"}



# nexus #5398: design-section atomic_phrases skip promotion without split: atomic.

_ATOMIC_PHRASE_JP = _decode("1 PR c3067_c540C_c6642")
_MIGRATION_ORDER_JP = _decode(
    "c79FB_c884C_c9806_c5E8F_: 1 PR "
    "c3067_c540C_c6642_c306B_c5165_c308C_c308B_"
)
# 21 files across two concerns so promotion is not declined (max_slices is 3).
_OVERSIZED_ROWS = [(f"src/a/f{i}.py", _MODIFY) for i in range(11)] + [
    (f"src/b/f{i}.py", _MODIFY) for i in range(10)
]


def _oversized_body_with_design(design_text: str, *, background_text: str = "") -> str:
    table = "".join(
        f"| `sumipan/issuesmith` | `{p}` | {k} | x |\n" for p, k in _OVERSIZED_ROWS
    )
    parts = [
        "```yaml\ntarget_repo: sumipan/issuesmith\nbase_branch: main\n```\n\n",
    ]
    if background_text:
        parts.append(f"## Background\n\n{background_text}\n\n")
    parts.append(f"## Design\n\n{design_text}\n\n")
    parts.append("## Changed Files\n\n" + _HEADER + table)
    return _decode("".join(parts))


def test_atomic_phrase_stops_promotion_and_check(tmp_path, monkeypatch):
    from issuesmith.gate_rules.scope_size import promote_oversized_issue_body

    _write_config(
        tmp_path,
        monkeypatch,
        {"scope_size": {"atomic_phrases": [_ATOMIC_PHRASE_JP]}},
    )
    body = _oversized_body_with_design(_MIGRATION_ORDER_JP)
    assert promote_oversized_issue_body(body) == body
    assert ScopeSizeRules().check(body, []) == []



def test_atomic_phrase_only_in_background_does_not_stop_promotion(tmp_path, monkeypatch):
    from issuesmith.gate_rules.scope_size import atomic_phrase, promote_oversized_issue_body

    _write_config(
        tmp_path,
        monkeypatch,
        {"scope_size": {"atomic_phrases": [_ATOMIC_PHRASE_JP]}},
    )
    body = _oversized_body_with_design(
        "no matching phrase",
        background_text=_MIGRATION_ORDER_JP,
    )
    assert atomic_phrase(body) is None
    assert promote_oversized_issue_body(body) != body




# --- Promotion AC YAML by change kind (#5750) ---


def _build_sub_block_for_rows(rows: list[tuple[str, str]]) -> str:
    from issuesmith.gate_rules.scope_size import _build_sub_block

    cfg = get_config()
    sections = cfg.sections
    full_rows = [
        ("sumipan/issuesmith", path, _decode(kind), "desc") for path, kind in rows
    ]
    return _build_sub_block(
        1,
        "concern-a",
        full_rows,
        target_repo="sumipan/issuesmith",
        subsections=cfg.sub_design_subsections,
        changed_label=sections["changed_files"],
        ac_label=sections["acceptance_criteria"],
    )


def _structured_ac_yaml(block: str) -> dict:
    match = re.search(r"```yaml\n(.*?)\n```", block, re.DOTALL)
    assert match, block
    return yaml.safe_load(match.group(1))





def test_build_sub_block_ac_empty_lists_use_brackets():
    block = _build_sub_block_for_rows([])
    ac = _structured_ac_yaml(block)
    assert ac["paths_must_exist"] == []
    assert ac["paths_must_not_exist"] == []
