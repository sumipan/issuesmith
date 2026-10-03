"""Language packs: loading, validation, replacement and legacy-key compat (nexus #4469)."""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import yaml

import issuesmith.config as config_module
from issuesmith import language
from issuesmith.config import ConfigError, get_config, load_config, reset_config_cache
from issuesmith.contract import (
    SUB_HEADER_RE,
    change_paths_for_repo,
    get_section,
    iter_sub_blocks,
    sub_header_re,
)
from issuesmith.language import EN, LanguagePack, language_pack_from_mapping, load_language_pack

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _real_en_default(monkeypatch):
    """Use the shipped EN pack as the package default (conftest swaps in a test pack)."""
    monkeypatch.setattr(config_module, "EN", EN)
    reset_config_cache()
    yield
    reset_config_cache()


def _pack_dict(pack: LanguagePack = EN, **overrides) -> dict:
    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(pack, f.name)
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, str):
            value = dict(value)
        data[f.name] = value
    data.update(overrides)
    return data


# ASCII vocabulary that differs from EN in every Issue-body field.
_CUSTOM_SECTIONS = {key: f"X {heading}" for key, heading in EN.sections.items()}
_CUSTOM = _pack_dict(
    sections=_CUSTOM_SECTIONS,
    sub_design_subsections=["Area", "Approach", "Touched Files", "Done When"],
    sub_header_prefix="Part",
    sub_plan_columns=["No", "Name", "Repo", "Work", "After"],
    change_table_columns=["Repo", "Path", "Kind", "Note"],
    no_deps_word="nil",
    delete_words=["remove"],
    new_words=["create"],
    removal_words=["strip"],
    placeholder_words=["FILLME"],
    vague_ac_words=["kinda works"],
    derived_from_phrase="spun off from",
    parent_issue_label="Parent",
    dependencies_table_header="| No | Needs | Status |",
    out_of_scope_heading="Not Doing",
    messages={key: f"[custom] {value}" for key, value in EN.messages.items()},
)


def _write_yaml(path: Path, payload: dict) -> Path:
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _use_config(tmp_path: Path, monkeypatch, payload: dict) -> None:
    cfg = _write_yaml(tmp_path / "issuesmith.yaml", {"repo": "example/app", **payload})
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()


# --- EN pack -----------------------------------------------------------------


def test_en_pack_is_ascii_and_matches_test_vocabulary():
    from tests.conftest import _ENGLISH_SECTIONS, _ENGLISH_SUBSECTIONS

    assert dict(EN.sections) == _ENGLISH_SECTIONS
    assert EN.sub_design_subsections == _ENGLISH_SUBSECTIONS
    assert all(text.isascii() for text in yaml.safe_dump(_pack_dict()).splitlines())


def test_en_is_the_only_shipped_pack():
    """No other LanguagePack instance (e.g. a Japanese pack) lives under src/."""
    defined = []
    for path in (_REPO_ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        defined += re.findall(r"^(\w+)\s*(?::[^=\n]+)?=\s*LanguagePack\(", text, re.MULTILINE)
        defined += re.findall(r"^(\w+)\s*=\s*(?:dataclasses\.)?replace\(\s*EN\b", text, re.MULTILINE)
    assert defined == ["EN"]
    assert not list((_REPO_ROOT / "src").rglob("*lang*.y*ml"))


def test_pack_defines_body_vocabulary_and_message_fields():
    names = {f.name for f in dataclasses.fields(LanguagePack)}
    assert {
        "sections", "sub_design_subsections", "sub_header_prefix", "sub_plan_columns",
        "change_table_columns", "no_deps_word", "delete_words", "new_words", "removal_words",
        "placeholder_words", "vague_ac_words", "derived_from_phrase", "parent_issue_label",
        "dependencies_table_header", "messages",
    } <= names
    assert EN.messages
    assert all(re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", key) for key in EN.messages)


def test_message_formats_keyword_template():
    pack = dataclasses.replace(EN, messages={"m.x": "{b} then {a}"})
    assert pack.message("m.x", a="1", b="2") == "2 then 1"


def test_default_config_uses_en(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch, {})
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        cfg = get_config()
    assert cfg.language is EN
    assert dict(cfg.sections) == dict(EN.sections)
    assert cfg.sub_design_subsections == EN.sub_design_subsections
    assert cfg.scope_size.delete_words == EN.delete_words
    assert cfg.scope_size.sub_plan_header == EN.sub_plan_header


# --- loading -----------------------------------------------------------------


def test_load_language_pack_returns_every_field(tmp_path):
    pack = load_language_pack(_write_yaml(tmp_path / "pack.yaml", _CUSTOM))
    assert _pack_dict(pack) == _CUSTOM


def test_language_pack_key_replaces_every_field(tmp_path, monkeypatch):
    (tmp_path / "packs").mkdir()
    _write_yaml(tmp_path / "packs" / "custom.yaml", _CUSTOM)
    _use_config(tmp_path, monkeypatch, {"language_pack": "packs/custom.yaml"})
    cfg = get_config()
    assert _pack_dict(cfg.language) == _CUSTOM
    assert dict(cfg.sections) == _CUSTOM_SECTIONS
    assert cfg.sub_design_subsections == ("Area", "Approach", "Touched Files", "Done When")
    assert cfg.scope_size.delete_words == ("remove",)
    assert cfg.scope_size.new_words == ("create",)
    assert cfg.scope_size.sub_plan_header == "| No | Name | Repo | Work | After |"
    assert cfg.scope_size.no_deps_word == "nil"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(unknown_field="x"), id="unknown-key"),
        pytest.param(lambda d: d.update(sub_header_prefix=["Sub"]), id="str-wrong-type"),
        pytest.param(lambda d: d.update(delete_words="delete"), id="list-wrong-type"),
        pytest.param(lambda d: d.update(new_words=[]), id="list-empty"),
        pytest.param(lambda d: d.update(sub_plan_columns=["#", "a"]), id="columns-length"),
        pytest.param(lambda d: d["sections"].update(extra="x"), id="section-unknown"),
        pytest.param(lambda d: d["messages"].update({"m.extra": "x"}), id="message-unknown"),
        pytest.param(lambda d: d["messages"].update({next(iter(d["messages"])): 3}), id="message-type"),
    ],
)
def test_invalid_pack_is_config_error(tmp_path, monkeypatch, mutate):
    data = _pack_dict()
    mutate(data)
    path = _write_yaml(tmp_path / "pack.yaml", data)
    with pytest.raises(ConfigError):
        load_language_pack(path)
    _use_config(tmp_path, monkeypatch, {"language_pack": str(path)})
    with pytest.raises(ConfigError):
        get_config()


def test_non_mapping_and_missing_file_are_config_errors(tmp_path):
    with pytest.raises(ConfigError):
        language_pack_from_mapping(["a"])
    with pytest.raises(ConfigError):
        load_language_pack(tmp_path / "absent.yaml")


def test_readme_lists_every_pack_field():
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for f in dataclasses.fields(LanguagePack):
        assert f"`{f.name}`" in readme, f.name


# --- legacy keys -------------------------------------------------------------


_LEGACY = {
    "sections": _CUSTOM_SECTIONS,
    "sub_design_subsections": ["Area", "Approach", "Touched Files", "Done When"],
    "scope_size": {
        "delete_words": ["remove"],
        "new_words": ["create"],
        "sub_plan_header": "| No | Name | Repo | Work | After |",
        "no_deps_word": "nil",
    },
}


def _vocab(cfg) -> tuple:
    return (
        dict(cfg.sections),
        cfg.sub_design_subsections,
        cfg.scope_size.delete_words,
        cfg.scope_size.new_words,
        cfg.scope_size.sub_plan_header,
        cfg.scope_size.no_deps_word,
    )


def test_legacy_keys_warn_and_match_pack_result(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch, _LEGACY)
    with pytest.warns(DeprecationWarning, match="language_pack"):
        legacy = get_config()

    (tmp_path / "p").mkdir()
    _write_yaml(tmp_path / "p" / "pack.yaml", _CUSTOM)
    _use_config(tmp_path, monkeypatch, {"language_pack": "p/pack.yaml"})
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        packed = get_config()

    assert _vocab(legacy) == _vocab(packed)
    assert legacy.language.sub_plan_columns == packed.language.sub_plan_columns
    # Fields without a legacy key keep the EN values.
    assert legacy.language.sub_header_prefix == EN.sub_header_prefix


@pytest.mark.parametrize(
    "payload",
    [
        {"scope_size": {"delete_words": ["remove"]}},
        {"scope_size": {"new_words": ["create"]}},
        {"scope_size": {"sub_plan_header": "| a | b |"}},
        {"scope_size": {"no_deps_word": "-"}},
        {"sections": {"design": "Plan"}},
        {"sub_design_subsections": ["A", "B", "C", "D"]},
    ],
)
def test_each_legacy_key_warns(tmp_path, monkeypatch, payload):
    _use_config(tmp_path, monkeypatch, payload)
    with pytest.warns(DeprecationWarning):
        load_config()


def test_pack_wins_over_legacy_keys_with_warning(tmp_path, monkeypatch):
    _write_yaml(tmp_path / "pack.yaml", _CUSTOM)
    _use_config(
        tmp_path,
        monkeypatch,
        {
            "language_pack": "pack.yaml",
            "sections": {"design": "Ignored"},
            "scope_size": {"delete_words": ["ignored"], "max_files": 5},
        },
    )
    with pytest.warns(DeprecationWarning, match="ignored because language_pack"):
        cfg = get_config()
    assert cfg.sections["design"] == _CUSTOM_SECTIONS["design"]
    assert cfg.scope_size.delete_words == ("remove",)
    assert cfg.scope_size.max_files == 5


# --- parsing with a non-default pack -----------------------------------------


def _body(pack: LanguagePack) -> str:
    sec = pack.sections
    subs = pack.sub_design_subsections
    repo_col, path_col, kind_col, note_col = pack.change_table_columns
    prefix = pack.sub_header_prefix
    table = (
        f"| {repo_col} | {path_col} | {kind_col} | {note_col} |\n|---|---|---|---|\n"
        "| `o/r` | `src/a.py` | x | y |\n| `o/r` | `tests/test_a.py` | x | y |\n"
    )
    return (
        f"## {sec['background']}\n\nwhy\n\n"
        f"## {sec['design']}\n\nintro\n\n"
        f"#### {prefix}1: first\n\n**{subs[0]}**: a\n\n**{sec['changed_files']}**:\n{table}\n"
        f"#### {prefix}2: second\n\n**{subs[0]}**: b\n\n"
        f"## {sec['acceptance_criteria']}\n\n- [ ] one\n"
    )


def _parse(body: str, pack: LanguagePack) -> tuple:
    sec = pack.sections
    blocks = iter_sub_blocks(body)
    return (
        get_section(body, sec["background"]),
        get_section(body, sec["acceptance_criteria"]),
        [m.group(1) for m in sub_header_re().finditer(body)],
        SUB_HEADER_RE.findall(body),
        [num for num, _ in blocks],
        [len(block.splitlines()) for _, block in blocks],
        change_paths_for_repo(body, "o/r"),
    )


def test_custom_pack_parses_like_default(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch, {})
    default = _parse(_body(EN), EN)

    _write_yaml(tmp_path / "pack.yaml", _CUSTOM)
    _use_config(tmp_path, monkeypatch, {"language_pack": "pack.yaml"})
    custom_pack = get_config().language
    custom = _parse(_body(custom_pack), custom_pack)

    assert custom == default
    assert default[2] == ["1", "2"]
    assert default[-1] == ["src/a.py", "tests/test_a.py"]
    # The default vocabulary no longer matches under the custom pack.
    assert sub_header_re().findall(_body(EN)) == []


def test_lazy_sub_header_re_follows_config(tmp_path, monkeypatch):
    _use_config(tmp_path, monkeypatch, {})
    text = "#### Sub1: a\n#### Part2: b\n"
    assert SUB_HEADER_RE.findall(text) == ["1"]
    assert SUB_HEADER_RE.search(text).group(1) == "1"
    assert SUB_HEADER_RE.match(text).group(1) == "1"
    assert [m.group(1) for m in SUB_HEADER_RE.finditer(text)] == ["1"]
    assert SUB_HEADER_RE.sub("X", text) == "X a\n#### Part2: b\n"
    assert SUB_HEADER_RE.pattern == sub_header_re().pattern

    _write_yaml(tmp_path / "pack.yaml", _CUSTOM)
    _use_config(tmp_path, monkeypatch, {"language_pack": "pack.yaml"})
    assert SUB_HEADER_RE.findall(text) == ["2"]


def test_contract_import_does_not_read_config(tmp_path):
    bad = _write_yaml(tmp_path / "pack.yaml", {"sections": {}})
    cfg = _write_yaml(tmp_path / "issuesmith.yaml", {"repo": "o/r", "language_pack": str(bad)})
    env = {**os.environ, "ISSUESMITH_CONFIG": str(cfg), "PYTHONPATH": str(_REPO_ROOT / "src")}
    code = (
        "import issuesmith.contract as c, issuesmith.body_editor, issuesmith.language;"
        " print(repr(c.SUB_HEADER_RE))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=120
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "lazy" in proc.stdout


# --- CJK-free modules ----------------------------------------------------------


@pytest.mark.parametrize("module", ["language", "config", "contract", "body_editor"])
def test_module_has_no_cjk(module):
    from tests.test_no_cjk import _CJK as cjk

    path = _REPO_ROOT / "src" / "issuesmith" / f"{module}.py"
    lines = [n for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if cjk.search(line)]
    assert lines == []


def test_language_module_exports():
    assert language.FIELD_NAMES == tuple(f.name for f in dataclasses.fields(LanguagePack))


def test_partial_pack_falls_back_to_english_without_mutation():
    data = {"sections": {"design": "Custom design"}, "messages": {}}
    pack = language_pack_from_mapping(data)
    assert pack.out_of_scope_heading == EN.out_of_scope_heading
    assert pack.sections["design"] == "Custom design"
    assert pack.sections["background"] == EN.sections["background"]
    assert dict(pack.messages) == dict(EN.messages)
    assert data == {"sections": {"design": "Custom design"}, "messages": {}}
    assert language_pack_from_mapping({}) == EN
