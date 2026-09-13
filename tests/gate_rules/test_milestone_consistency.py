"""tests/gate_rules/test_milestone_consistency.py — milestone_consistency ゲート。"""
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
    for v in vs:
        assert v.severity == "fail"
        assert v.auto_fixable is True
        assert v.fix_hint


def test_fixture_with_milestone_label_skips_label_missing():
    vs = _check(_FIXTURE, ["scope:milestone"])
    assert "milestone_consistency.label_missing" not in _ids(vs)
    assert "milestone_consistency.sub_header_english" in _ids(vs)
    assert "milestone_consistency.sub_plan_misplaced" in _ids(vs)


def test_plain_issue_without_split_plan_has_no_violations():
    body = (
        '```yaml\n'
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "**"\n'
        "```\n\n"
        "## 設計\n通常の単一 Issue 設計\n"
    )
    assert _check(body, []) == []


def test_normalized_japanese_headers_with_milestone_label_pass():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```

## 設計

#### サブ1: foo

**スコープ**: a
**設計方針**: b
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/nexus` | `src/a.py` | 新規 | x |

**受け入れ条件**:
- [ ] one
- [ ] two
- [ ] three

## マイルストーン

### サブイシュー分割計画
| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | foo | scope | なし |
"""
    assert _check(body, ["scope:milestone"]) == []


def test_ac6_normalized_fixture_not_sub_plan_missing():
    """AC-6: normalize + relocate 後は b1_milestone_subdesign.sub_plan_missing を出さない。"""
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
