"""tests/gate_rules/test_milestone_consistency.py — milestone_consistency gate."""
from __future__ import annotations

from pathlib import Path

import issuesmith.gate_rules.milestone_consistency  # noqa: F401
from issuesmith.body_editor import normalize_sub_headers, relocate_sub_plan
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.b1_milestone_subdesign import B1MilestoneSubdesignRules

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "issue_3059_original.md"
).read_text(encoding="utf-8")


def _check(body: str, labels: list[str]):
    return GATE_REGISTRY["milestone_consistency"]().check(body, labels)


def _ids(violations) -> set[str]:
    return {v.rule_id for v in violations}


def test_fixture_without_milestone_label_detects_all_three():
    vs = _check(_FIXTURE, [])
    assert _ids(vs) == {
        "milestone_consistency.label_missing",
        "milestone_consistency.sub_header_english",
        "milestone_consistency.sub_plan_misplaced",
    }
    by_id = {v.rule_id: v for v in vs}
    assert by_id["milestone_consistency.label_missing"].auto_fixable is False
    for rule_id in (
        "milestone_consistency.sub_header_english",
        "milestone_consistency.sub_plan_misplaced",
    ):
        v = by_id[rule_id]
        assert v.severity == "fail"
        assert v.auto_fixable is True
        assert v.fix_hint


def test_fixture_with_milestone_label_skips_label_missing():
    vs = _check(_FIXTURE, ["scope:milestone"])
    assert "milestone_consistency.label_missing" not in _ids(vs)
    assert "milestone_consistency.sub_header_english" in _ids(vs)
    assert "milestone_consistency.sub_plan_misplaced" in _ids(vs)


def test_plain_issue_without_split_plan_has_no_violations():
    # ASCII fixture data.
    body = (
        '```yaml\n'
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "**"\n'
        "```\n\n"
        "## Design\nc901A_c5E38_c306E_c5358_c4E00 Issue Design\n"
    )
    assert _check(body, []) == []


def test_normalized_japanese_headers_with_milestone_label_pass():
    # ASCII fixture data.
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```

## Design

#### Sub1: foo

**Scope**: a
**Design Policy**: b
**Changed Files**:
| Repository | File Path | Change Type | Description |
|---|---|---|---|
| `sumipan/nexus` | `src/a.py` | Add | x |

**Acceptance Criteria**:
- [ ] one
- [ ] two
- [ ] three

## Milestone

### Sub-issue Plan
| # | Title | c5185_c5BB9 | Dependency |
|---|--------|------|------|
| 1 | foo | scope | None |
"""
    assert _check(body, ["scope:milestone"]) == []


def test_ac6_normalized_fixture_not_sub_plan_missing():
    """AC-6: after normalize + relocate, no b1_milestone_subdesign.sub_plan_missing."""
    normalized = relocate_sub_plan(normalize_sub_headers(_FIXTURE))
    vs = B1MilestoneSubdesignRules().check(normalized, ["scope:milestone"])
    assert not any(v.rule_id == "b1_milestone_subdesign.sub_plan_missing" for v in vs)


def test_registry_exposes_milestone_consistency():
    assert "milestone_consistency" in GATE_REGISTRY


def test_fix_label_missing_adds_label_and_milestone_object():
    """AC-6: label_missing auto-fix creates milestone object too."""
    from issuesmith.gate_rules.milestone_consistency import fix_label_missing

    class _Client:
        def __init__(self):
            self.labels: set[str] = set()
            self.milestones: list[dict] = []
            self.issue_milestone = None
            self.updates: list[dict] = []
            self._next = 50

        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "labels": [{"name": n} for n in sorted(self.labels)],
                "milestone": self.issue_milestone,
            }

        def issue_update(self, number, **kwargs):
            self.updates.append({"number": number, **kwargs})
            for lab in kwargs.get("labels_add") or []:
                self.labels.add(lab)
            if kwargs.get("milestone") is not None:
                self.issue_milestone = {
                    "number": kwargs["milestone"],
                    "title": f"{number}-attached",
                }

        def milestone_list(self):
            return list(self.milestones)

        def milestone_create(self, title, description=""):
            num = self._next
            self._next += 1
            self.milestones.append({"number": num, "title": title})
            return num

    client = _Client()
    title = fix_label_missing(client, 3130)
    assert "scope:milestone" in client.labels
    assert title.startswith("3130-")
    assert client.issue_milestone is not None
    assert any(u.get("labels_add") == ["scope:milestone"] for u in client.updates)


def test_label_missing_fix_hint_mentions_milestone_object():
    vs = _check(_FIXTURE, [])
    label_v = next(v for v in vs if v.rule_id == "milestone_consistency.label_missing")
    assert "milestone" in (label_v.fix_hint or "").lower()
    assert "fix_label_missing" in (label_v.fix_hint or "")


def test_label_missing_fix_hint_requires_sub_design_blocks():
    from tests.legacy_text import SUB

    vs = _check(_FIXTURE, [])
    label_v = next(v for v in vs if v.rule_id == "milestone_consistency.label_missing")
    assert "fix_label_missing" in (label_v.fix_hint or "")
    assert "b1_milestone_subdesign" in (label_v.fix_hint or "")
    assert f"#### {SUB}N:" in (label_v.fix_hint or "")
    assert "#### Sub N:" not in (label_v.fix_hint or "")


def test_apply_body_autofixes_normalizes_english_and_relocates_plan():
    from issuesmith.gate_rules.milestone_consistency import apply_body_autofixes
    from tests.legacy_text import SUB

    body, applied = apply_body_autofixes(_FIXTURE)
    assert "milestone_consistency.sub_header_english" in applied
    assert "milestone_consistency.sub_plan_misplaced" in applied
    assert "#### Sub " not in body
    assert f"#### {SUB}" in body
    # Idempotent
    again, applied2 = apply_body_autofixes(body)
    assert again == body
    assert applied2 == []


def test_label_missing_is_not_auto_fixable():
    vs = _check(_FIXTURE, [])
    label_v = next(v for v in vs if v.rule_id == "milestone_consistency.label_missing")
    assert label_v.auto_fixable is False


def test_apply_auto_fixable_helpers_skips_label_missing():
    from issuesmith.gate_rules.milestone_consistency import (
        MilestoneConsistencyRules,
        apply_auto_fixable_helpers,
    )

    class _Client:
        def __init__(self):
            self.labels: set[str] = set()
            self.milestones: list[dict] = []
            self.issue_milestone = None
            self._next = 50

        def issue_get(self, number, fields=None):
            return {
                "number": number,
                "labels": [{"name": n} for n in sorted(self.labels)],
                "milestone": self.issue_milestone,
            }

        def issue_update(self, number, **kwargs):
            for lab in kwargs.get("labels_add") or []:
                self.labels.add(lab)
            if kwargs.get("milestone") is not None:
                self.issue_milestone = {
                    "number": kwargs["milestone"],
                    "title": f"{number}-attached",
                }

        def milestone_list(self):
            return list(self.milestones)

        def milestone_create(self, title, description=""):
            num = self._next
            self._next += 1
            self.milestones.append({"number": num, "title": title})
            return num

    client = _Client()
    vs = MilestoneConsistencyRules().check(_FIXTURE, [])
    body, labels, applied = apply_auto_fixable_helpers(
        client, 4191, _FIXTURE, [], vs
    )
    assert "milestone_consistency.label_missing" not in applied
    assert "scope:milestone" not in labels
    assert "scope:milestone" not in client.labels
    assert client.issue_milestone is None
    assert "milestone_consistency.sub_header_english" in applied
    assert "#### Sub " not in body


# --- Language pack vocabulary (nexus #4474) ---


def _use_pack(tmp_path, monkeypatch, **overrides) -> None:
    """Point the config at a language pack YAML: the EN pack with ``overrides``."""
    import dataclasses

    import yaml

    from issuesmith.config import reset_config_cache
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


_SUB_ONLY_BODY = "## Design\n\n#### {prefix}1: foo\n\nbody\n"


def test_ascii_pack_sub_header_matches_en_decision(tmp_path, monkeypatch):
    en_dir = tmp_path / "en"
    en_dir.mkdir()
    _use_pack(en_dir, monkeypatch)
    en_ids = _ids(_check(_SUB_ONLY_BODY.format(prefix="Sub"), []))

    ascii_dir = tmp_path / "ascii"
    ascii_dir.mkdir()
    _use_pack(ascii_dir, monkeypatch, sub_header_prefix="Part")
    ascii_ids = _ids(_check(_SUB_ONLY_BODY.format(prefix="Part"), []))

    assert en_ids == ascii_ids == {"milestone_consistency.label_missing"}


def test_label_missing_fix_hint_uses_pack_prefix(tmp_path, monkeypatch):
    _use_pack(tmp_path, monkeypatch, sub_header_prefix="Part")
    vs = _check(_SUB_ONLY_BODY.format(prefix="Part"), [])
    label_v = next(v for v in vs if v.rule_id == "milestone_consistency.label_missing")
    assert "#### PartN:" in (label_v.fix_hint or "")


def test_ascii_pack_does_not_read_other_sub_prefix(tmp_path, monkeypatch):
    _use_pack(tmp_path, monkeypatch, sub_header_prefix="Part")
    assert _check(_SUB_ONLY_BODY.format(prefix="Chapter"), []) == []
