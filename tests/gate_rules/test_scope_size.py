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
    assert "concerns=4/2" in concerns
    assert "tools/issuesmith_steps" in concerns
    mixed = by_id["scope_size.delete_with_new"].message
    assert "workflows/issuesmith/cp1-gate.md" in mixed
    assert "workflows/issuesmith/repair.md" in mixed


def test_original_fixture_measure():
    measure = measure_size(_fixture("issue_3627_original.md"))
    assert len(measure.files) == 10
    assert list(measure.concerns) == [
        "workflows",
        ".",
        "workflows/issuesmith",
        "tools/issuesmith_steps",
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


def test_config_thresholds_are_honoured(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"scope_size": {"max_files": 20, "max_concerns": 4, "delete_with_new": True}},
    )
    assert ScopeSizeRules().check(_fixture("issue_3627_original.md"), []) == []
