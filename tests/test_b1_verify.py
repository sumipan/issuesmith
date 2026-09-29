"""test_b1_verify.py — unit tests for B1 deterministic Verify (#2541)."""
from __future__ import annotations

from issuesmith.b1_verify import collect_violations, format_report

_VALID_BODY = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    'scope_gate:\n'
    '  enabled: false\n'
    '```\n\n'
    "## Overview\nclean body\n"
)


def test_valid_body_has_no_violations():
    assert collect_violations(_VALID_BODY, []) == []


def test_missing_yaml_is_reported():
    violations = collect_violations("## Overview\nno yaml\n", [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)


def test_intentional_hold_is_excluded():
    """scope:milestone intentional_hold is not a B1 artifact defect; exclude it"""
    violations = collect_violations(_VALID_BODY, ["scope:milestone"])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_migration_rules_are_included():
    violations = collect_violations(_VALID_BODY, ["scope:migration"])
    assert any(v.rule_id.startswith("b1_migration.") for v in violations)


def test_complete_migration_body_satisfies_ac_format_and_migration_gates():
    """One migration body must be able to satisfy every B1 gate at once (nexus #3899)."""
    from tests.gate_rules.test_b1_migration import BODY_COMPLETE

    rule_ids = {v.rule_id for v in collect_violations(_VALID_BODY + "\n" + BODY_COMPLETE, ["scope:migration"])}
    assert not {r for r in rule_ids if r.startswith(("b1_ac_format.", "b1_migration."))}, rule_ids


def test_format_report_shape():
    violations = collect_violations("## Overview\nno yaml\n", [])
    report = format_report(violations)
    assert report.startswith("VERIFY_FAILED_CHECKS: ")
    assert "## cp1.yaml_contract.missing_block" in report
    assert "FIX_HINT:" in report


def test_format_report_empty():
    assert format_report([]) == "VERIFY_FAILED_CHECKS: (none)\n"


def test_scope_size_violations_are_collected(tmp_path, monkeypatch):
    """scope_size runs in B1 Verify: the #3627 original fixture is reported (nexus #3665)."""
    import re
    from pathlib import Path

    import yaml

    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    raw = (
        Path(__file__).resolve().parent / "fixtures" / "issue_3627_original.md"
    ).read_text(encoding="utf-8")
    body = re.sub(r"c([0-9A-F]{4})_?", lambda m: chr(int(m.group(1), 16)), raw)
    try:
        violations = collect_violations(body, [])
    finally:
        reset_config_cache()
    assert any(v.rule_id.startswith("scope_size.") for v in violations)


_PROMOTED_MILESTONE_BODY = _VALID_BODY + (
    "\n## Design\nsingle-issue design written before the milestone promotion\n\n"
    "## Milestone\n\n"
    "### Sub-issue Plan\n"
    "| # | Title | Content | Dependency |\n"
    "|---|---|---|---|\n"
    "| 1 | a | scope a | None |\n"
    "| 2 | b | scope b | 1 |\n"
    "| 3 | c | scope c | 1 |\n"
)


def test_milestone_subdesign_gate_is_registered():
    from issuesmith.b1_verify import _GATES

    assert "b1_milestone_subdesign" in _GATES


def test_promoted_milestone_without_sub_blocks_fails_verify():
    """Regression (nexus #4002): 3 plan rows vs 0 sub blocks must not reach draft-done."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, ["scope:milestone"])}
    assert "b1_milestone_subdesign.sub_count_mismatch" in rule_ids


def test_non_milestone_verify_shows_label_missing_and_subdesign_together():
    """R1: split-plan body without scope:milestone label shows both label_missing and sub_count_mismatch."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, [])}
    assert "milestone_consistency.label_missing" in rule_ids, rule_ids
    assert "b1_milestone_subdesign.sub_count_mismatch" in rule_ids, rule_ids


def test_no_split_plan_body_ignores_milestone_subdesign():
    """R1: body without a split-plan pattern does not produce milestone violations without a label."""
    rule_ids = {v.rule_id for v in collect_violations(_VALID_BODY, [])}
    milestone_ids = {r for r in rule_ids if r.startswith(("b1_milestone_subdesign.", "cp1.milestone."))}
    assert not milestone_ids, milestone_ids


def test_assumed_labels_adds_scope_milestone_when_plan_present():
    """assumed_labels: split-plan body with no label → returns list with scope:milestone appended."""
    from issuesmith.b1_verify import assumed_labels

    result = assumed_labels(_PROMOTED_MILESTONE_BODY, [])
    assert result == ["scope:milestone"]


def test_assumed_labels_noop_when_label_already_present():
    """assumed_labels: already has scope:milestone → returns copy unchanged."""
    from issuesmith.b1_verify import assumed_labels

    result = assumed_labels(_PROMOTED_MILESTONE_BODY, ["scope:milestone"])
    assert result == ["scope:milestone"]


def test_assumed_labels_noop_when_no_split_plan():
    """assumed_labels: no split-plan pattern → returns copy of labels unchanged."""
    from issuesmith.b1_verify import assumed_labels

    result = assumed_labels(_VALID_BODY, ["scope:feature"])
    assert result == ["scope:feature"]


def test_assumed_labels_does_not_mutate_input():
    """assumed_labels: never modifies the input list."""
    from issuesmith.b1_verify import assumed_labels

    original = []
    result = assumed_labels(_PROMOTED_MILESTONE_BODY, original)
    assert original == [], "input list was mutated"
    assert result != original


def test_cp1_intentional_hold_excluded_with_assumed_labels():
    """R1: cp1.intentional_hold must not appear even when scope:milestone is assumed."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, [])}
    assert "cp1.intentional_hold" not in rule_ids


def test_complete_milestone_body_passes_milestone_subdesign():
    from tests.gate_rules.test_b1_milestone_subdesign import _valid_body

    rule_ids = {v.rule_id for v in collect_violations(_valid_body(), ["scope:milestone"])}
    assert not {r for r in rule_ids if r.startswith("b1_milestone_subdesign.")}, rule_ids


class _FakeForge:
    """Minimal ForgePort stand-in for deterministic recovery tests."""

    def __init__(self, body: str = "", labels: list[str] | None = None):
        self.body = body
        self.labels: set[str] = set(labels or [])
        self.milestones: list[dict] = []
        self.issue_milestone = None
        self.updates: list[dict] = []
        self._next = 90

    def issue_get(self, number, fields=None):
        return {
            "number": number,
            "body": self.body,
            "labels": [{"name": n} for n in sorted(self.labels)],
            "milestone": self.issue_milestone,
        }

    def issue_update(self, number, **kwargs):
        self.updates.append({"number": number, **kwargs})
        if kwargs.get("body") is not None:
            self.body = kwargs["body"]
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


def _oversized_body() -> str:
    from tests.legacy_text import CHANGE_TYPE, DESCRIPTION, FILE_PATH, REPOSITORY

    header = f"| {REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION} |"
    rows = [
        ("src/a/f1.py", "Modify"),
        ("src/a/f2.py", "Modify"),
        ("src/a/f3.py", "Modify"),
        ("src/b/f1.py", "Modify"),
        ("src/b/f2.py", "Modify"),
        ("src/b/f3.py", "Modify"),
        ("src/c/f1.py", "Modify"),
        ("src/c/f2.py", "Modify"),
        ("src/c/f3.py", "Modify"),
    ]
    table = "\n".join(
        f"| `sumipan/nexus` | `{path}` | {kind} | x |" for path, kind in rows
    )
    return (
        '```yaml\n'
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - src/**\n"
        "scope_gate:\n"
        "  enabled: false\n"
        "```\n\n"
        "## Design\nsingle-issue design before promotion\n\n"
        f"## Changed Files\n{header}\n|---|---|---|---|\n{table}\n"
    )


def test_deterministic_recovery_promotes_oversized_issue_in_one_pass(tmp_path, monkeypatch):
    """AC (#4191): scope_size overflow → milestone contract in one recovery."""
    import yaml

    from issuesmith.b1_verify import apply_deterministic_recovery
    from issuesmith.config import reset_config_cache
    from tests.legacy_text import SUB

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    body = _oversized_body()
    client = _FakeForge(body=body)
    try:
        result = apply_deterministic_recovery(client, 4191, body, [], persist=True)
    finally:
        reset_config_cache()

    assert "scope:milestone" in result.labels
    assert "scope:milestone" in client.labels
    assert client.issue_milestone is not None
    assert "scope_size.promote_to_milestone" in result.applied
    assert "milestone_consistency.fix_label_missing" in result.applied
    assert f"#### {SUB}" in result.body
    assert "#### Sub " not in result.body
    assert result.body.lstrip().startswith("```yaml")
    assert "target_repo: sumipan/nexus" in result.body
    assert "Milestone" in result.body or "milestone" in result.body.lower()
    # Auto-fixable helpers ran; only non-auto remain for LLM (may be empty).
    assert all(not v.auto_fixable for v in result.llm_violations)
    assert not any(v.rule_id.startswith("scope_size.") for v in result.remaining)
    assert not any(
        v.rule_id == "milestone_consistency.sub_header_english" for v in result.remaining
    )
    assert not any(
        v.rule_id == "milestone_consistency.label_missing" for v in result.remaining
    )
    assert not any(
        v.rule_id.startswith("b1_milestone_subdesign.") for v in result.remaining
    )


def test_verify_b1_cli_applies_deterministic_recovery_by_default(
    tmp_path, monkeypatch, capsys
):
    """The production ``verify b1`` path runs helpers before LLM recovery."""
    import sys

    import yaml

    from issuesmith.cli import _cmd_verify
    from issuesmith.config import reset_config_cache
    from tests.legacy_text import SUB

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    client = _FakeForge(body=_oversized_body())
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: client)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    try:
        _cmd_verify(["b1", "4191"])
    finally:
        reset_config_cache()

    output = capsys.readouterr().out
    assert "DETERMINISTIC_APPLIED:" in output
    assert "scope_size.promote_to_milestone" in output
    assert "scope:milestone" in client.labels
    assert client.issue_milestone is not None
    assert f"#### {SUB}" in client.body
    assert "#### Sub " not in client.body


def test_deterministic_recovery_is_idempotent(tmp_path, monkeypatch):
    """AC (#4191): re-running recovery does not duplicate body/label/milestone."""
    import yaml

    from issuesmith.b1_verify import apply_deterministic_recovery
    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    body = _oversized_body()
    client = _FakeForge(body=body)
    try:
        first = apply_deterministic_recovery(client, 4191, body, [], persist=True)
        label_updates = sum(
            1 for u in client.updates if u.get("labels_add") == ["scope:milestone"]
        )
        body_after = first.body
        milestone_count = len(client.milestones)
        second = apply_deterministic_recovery(
            client, 4191, body_after, first.labels, persist=True
        )
    finally:
        reset_config_cache()

    assert second.body == body_after
    assert second.labels == first.labels
    assert len(client.milestones) == milestone_count
    # Second pass should not add another scope:milestone label write that grows state.
    assert sum(
        1 for u in client.updates if u.get("labels_add") == ["scope:milestone"]
    ) >= label_updates


def test_deterministic_recovery_skips_normal_sized_issue(tmp_path, monkeypatch):
    """AC (#4191): scope_size-ineligible Issues are unchanged by promotion."""
    import yaml

    from issuesmith.b1_verify import apply_deterministic_recovery
    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    client = _FakeForge(body=_VALID_BODY)
    try:
        result = apply_deterministic_recovery(
            client, 4191, _VALID_BODY, [], persist=True
        )
    finally:
        reset_config_cache()

    assert "scope_size.promote_to_milestone" not in result.applied
    assert result.body == _VALID_BODY
    assert "scope:milestone" not in result.labels


def test_deterministic_recovery_reports_unresolved_when_same_rule_remains(
    tmp_path, monkeypatch
):
    """AC (#4191): can_done is False and reason lists remaining rule_ids."""
    import yaml

    from issuesmith.b1_verify import apply_deterministic_recovery
    from issuesmith.config import reset_config_cache

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    # Missing yaml → cp1 violation that deterministic recovery cannot fix.
    body = "## Overview\nno yaml\n"
    client = _FakeForge(body=body)
    try:
        result = apply_deterministic_recovery(client, 4191, body, [], persist=True)
    finally:
        reset_config_cache()

    assert result.can_done is False
    assert result.unresolved_reason is not None
    assert "cp1.yaml_contract.missing_block" in result.unresolved_reason
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in result.remaining)
