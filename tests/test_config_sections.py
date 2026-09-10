"""sections: / sub_design_subsections: — 無設定は現行動作、カスタムは反映。"""

from __future__ import annotations

import re

import pytest
import yaml

from issuesmith.ac_contract import extract_contract_from_body
from issuesmith.b1_tier import determine_b1_tier
from issuesmith.config import get_config, load_config, reset_config_cache
from issuesmith.dep_extractor import extract_dependencies
from issuesmith.gate_rules.b1_ac_format import get_ac_section
from issuesmith.gate_rules.b1_milestone_subdesign import B1MilestoneSubdesignRules
from issuesmith.gate_rules.m2 import (
    get_unchecked_count,
    has_acceptance_criteria_section,
)
from issuesmith.m2_gate import has_acceptance_criteria_section as m2_gate_has_section


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


_DEFAULT_SECTIONS = {
    "acceptance_criteria": "受け入れ条件",
    "migration": "マイグレーション手順",
    "migration_state_survey": "実行時状態の調査",
    "sub_plan": "サブイシュー分割計画",
    "design": "設計",
    "background": "背景・目的",
    "dependencies": "依存（先行）",
    "impact_survey": "影響範囲調査",
    "milestone": "マイルストーン",
    "changed_files": "変更対象ファイル",
}
_DEFAULT_SUB_DESIGN = ("スコープ", "設計方針", "変更対象ファイル", "受け入れ条件")


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _builtin_defaults(tmp_path, monkeypatch) -> None:
    """repo のみの最小設定（その他はパッケージ既定）。"""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/app"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_default_sections_match_legacy_when_unset(tmp_path, monkeypatch):
    _builtin_defaults(tmp_path, monkeypatch)
    cfg = load_config()

    assert dict(cfg.sections) == _DEFAULT_SECTIONS
    assert cfg.sub_design_subsections == _DEFAULT_SUB_DESIGN

    body = (
        "## 背景・目的\n\n背景\n\n"
        "## 設計\n\n設計本文\n\n"
        "## 受け入れ条件\n\n"
        "```yaml\npaths_must_exist:\n  - a.py\n```\n"
        "- [ ] item\n"
    )
    assert determine_b1_tier(body) == "heavy"
    assert get_ac_section(body) is not None
    assert has_acceptance_criteria_section(body)
    assert m2_gate_has_section(body)
    assert get_unchecked_count(body) == 1
    assert extract_contract_from_body(body) == {"paths_must_exist": ["a.py"]}

    dep_body = (
        "## 依存（先行）\n\n"
        "| # | 依存先 | 状態 |\n|---|---|---|\n| 1 | #1234 | OPEN |\n"
    )
    assert extract_dependencies(dep_body) == [1234]


def test_custom_acceptance_criteria_propagates_to_consumers(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "sections": {"acceptance_criteria": "AC"},
        },
    )
    cfg = get_config()
    assert cfg.sections["acceptance_criteria"] == "AC"
    # 他キーは既定のまま（部分上書き）
    assert cfg.sections["design"] == "設計"

    body_legacy = "## 受け入れ条件\n\n```yaml\npaths_must_exist:\n  - a.py\n```\n"
    body_custom = "## AC\n\n```yaml\npaths_must_exist:\n  - a.py\n```\n- [ ] todo\n"

    assert get_ac_section(body_legacy) is None
    assert get_ac_section(body_custom) is not None
    assert not has_acceptance_criteria_section(body_legacy)
    assert has_acceptance_criteria_section(body_custom)
    assert not m2_gate_has_section(body_legacy)
    assert m2_gate_has_section(body_custom)
    assert extract_contract_from_body(body_legacy) is None
    assert extract_contract_from_body(body_custom) == {"paths_must_exist": ["a.py"]}
    assert get_unchecked_count(body_custom) == 1


def test_re_escape_allows_special_chars_in_section_heading(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "sections": {"acceptance_criteria": "AC (v2)"},
        },
    )
    body = (
        "## AC (v2)\n\n"
        "```yaml\npaths_must_exist:\n  - escaped.py\n```\n"
    )
    assert get_ac_section(body) is not None
    assert extract_contract_from_body(body) == {"paths_must_exist": ["escaped.py"]}
    # 括弧がリテラルとして扱われること（正規表現グループにならない）
    heading = get_config().sections["acceptance_criteria"]
    pattern = rf"^##\s+{re.escape(heading)}\s*\n"
    assert re.search(pattern, body, re.MULTILINE)


def test_custom_background_and_design_affect_b1_tier(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "sections": {
                "background": "Background",
                "design": "Design",
            },
        },
    )
    assert determine_b1_tier("## 背景・目的\n\nx\n") == "light"
    assert determine_b1_tier("## Background\n\nx\n") == "heavy"
    assert determine_b1_tier("## Design\n\nx\n") == "heavy"


def test_custom_dependencies_section(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "sections": {"dependencies": "Deps"},
        },
    )
    legacy = (
        "## 依存（先行）\n\n"
        "| # | 依存先 |\n|---|---|\n| 1 | #99 |\n"
    )
    custom = (
        "## Deps\n\n"
        "| # | 依存先 |\n|---|---|\n| 1 | #88 |\n"
    )
    assert extract_dependencies(legacy) == []
    assert extract_dependencies(custom) == [88]


def test_sub_design_subsections_default_and_custom(tmp_path, monkeypatch):
    _builtin_defaults(tmp_path, monkeypatch)
    assert load_config().sub_design_subsections == _DEFAULT_SUB_DESIGN

    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "sub_design_subsections": ["Scope", "Plan", "Files", "AC"],
        },
    )
    assert get_config().sub_design_subsections == ("Scope", "Plan", "Files", "AC")

    # 必須サブセクション欠落を custom 名で検出
    body = (
        "## マイルストーン\n\n"
        "### サブイシュー分割計画\n\n"
        "| # | タイトル |\n|---|---|\n| 1 | A |\n\n"
        "## 設計\n\n"
        "#### サブ1: A\n\n"
        "**Scope**:\n\nok\n\n"
        "**Plan**:\n\nok\n\n"
        "**Files**:\n\n"
        "| リポジトリ | ファイルパス | 変更種別 | 変更内容 |\n"
        "|---|---|---|---|\n"
        "| `r` | `a.py` | 修正 | x |\n\n"
        "**AC**:\n\n"
        "- [ ] one\n- [ ] two\n- [ ] three\n"
    )
    # custom subsections 揃い → subsection_missing 無し
    v = B1MilestoneSubdesignRules().check(body, ["scope:milestone"])
    missing = [x for x in v if x.rule_id == "b1_milestone_subdesign.subsection_missing"]
    assert missing == []
