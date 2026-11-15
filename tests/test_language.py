"""Language packs: loading, validation, replacement and legacy-key compat (nexus #4469)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml

import issuesmith.config as config_module
from issuesmith import language
from issuesmith.config import ConfigError, get_config, load_config, reset_config_cache
from issuesmith.contract import SUB_HEADER_RE, sub_header_re
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
    order_after_words=["following"],
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


def test_message_formats_keyword_template():
    pack = dataclasses.replace(EN, messages={"m.x": "{b} then {a}"})
    assert pack.message("m.x", a="1", b="2") == "2 then 1"


# --- loading -----------------------------------------------------------------


def test_load_language_pack_returns_every_field(tmp_path):
    pack = load_language_pack(_write_yaml(tmp_path / "pack.yaml", _CUSTOM))
    assert _pack_dict(pack) == _CUSTOM


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.update(unknown_field="x"), id="unknown-key"),
        pytest.param(lambda d: d.update(sub_header_prefix=["Sub"]), id="str-wrong-type"),
        pytest.param(lambda d: d.update(delete_words="delete"), id="list-wrong-type"),
        pytest.param(lambda d: d.update(new_words=[]), id="list-empty"),
        pytest.param(lambda d: d.update(order_after_words=[]), id="order-words-empty"),
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


def test_order_after_words_en_value_and_fallback():
    assert EN.order_after_words == ("after", "once", "depends on")
    data = _pack_dict()
    del data["order_after_words"]
    assert language_pack_from_mapping(data).order_after_words == EN.order_after_words


def test_default_messages_omit_legacy_step_and_sub1_keys():
    for key in EN.messages:
        assert not key.startswith("p0_worktree.")
        assert not key.startswith("m1_merge.")
        assert not key.startswith("m2_finalize.")
        assert not key.startswith("milestone.sub1_")
