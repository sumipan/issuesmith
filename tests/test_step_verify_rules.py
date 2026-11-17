"""test_step_verify.py — unit tests for B1 deterministic Verify (#2541)."""
from __future__ import annotations

from issuesmith.step_verify import DESIGN_DOC_GATES, collect_violations, format_report

_VALID_BODY = (
    '```yaml\n'
    'target_repo: sumipan/issuesmith\n'
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


def test_deps_format_gate_is_in_step_verify_gates():
    assert "deps_format" in DESIGN_DOC_GATES


def test_collect_violations_reports_unparsed_dependency_section():
    body = _VALID_BODY + "\n## Dependencies\n\nsee #999 in prose\n"
    violations = collect_violations(body, [])
    assert any(v.rule_id == "deps.unparsed_dependency_section" for v in violations)
    report = format_report(violations)
    assert "deps.unparsed_dependency_section" in report


def test_collect_violations_self_issue_skips_self_unparsed():
    body = _VALID_BODY + "\n## Dependencies\n\ncontext #5061 only\n"
    violations = collect_violations(body, [], issue_number=5061)
    assert not any(v.rule_id == "deps.unparsed_dependency_section" for v in violations)


def test_missing_yaml_is_reported():
    violations = collect_violations("## Overview\nno yaml\n", [])
    assert any(v.rule_id == "design_lint.yaml_contract.missing_block" for v in violations)


def test_intentional_hold_is_excluded():
    """scope:milestone intentional_hold is not a B1 artifact defect; exclude it"""
    violations = collect_violations(_VALID_BODY, ["scope:milestone"])
    assert not any(v.rule_id == "design_lint.intentional_hold" for v in violations)


def test_migration_rules_are_included():
    violations = collect_violations(_VALID_BODY, ["scope:migration"])
    assert any(v.rule_id.startswith("migration_plan.") for v in violations)


def test_complete_migration_body_satisfies_ac_format_and_migration_gates():
    """One migration body must be able to satisfy every B1 gate at once (nexus #3899)."""
    from tests.gate_rules.test_migration_plan import BODY_COMPLETE

    rule_ids = {v.rule_id for v in collect_violations(_VALID_BODY + "\n" + BODY_COMPLETE, ["scope:migration"])}
    assert not {r for r in rule_ids if r.startswith(("ac_format.", "migration_plan."))}, rule_ids


def test_format_report_shape():
    violations = collect_violations("## Overview\nno yaml\n", [])
    report = format_report(violations)
    assert report.startswith("VERIFY_FAILED_CHECKS: ")
    assert "## design_lint.yaml_contract.missing_block" in report
    assert "FIX_HINT:" in report


def test_format_report_empty():
    assert format_report([]) == "VERIFY_FAILED_CHECKS: (none)\n"




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
    from issuesmith.step_verify import DESIGN_DOC_GATES

    assert "milestone_subdesign" in DESIGN_DOC_GATES


def test_pin_bump_gate_is_registered():
    from issuesmith.step_verify import DESIGN_DOC_GATES

    assert "pin_bump" in DESIGN_DOC_GATES


def test_change_table_scope_gate_is_registered():
    from issuesmith.step_verify import DESIGN_DOC_GATES

    assert "change_table_scope" in DESIGN_DOC_GATES


def test_change_table_scope_imported_in_gate_registry():
    import issuesmith.gate_rules  # noqa: F401 — side-effect registration
    from issuesmith.gate_rules import GATE_REGISTRY

    assert "change_table_scope" in GATE_REGISTRY


def test_5581_recovery_body_fails_change_table_scope(tmp_path, monkeypatch):
    import yaml

    from issuesmith.config import reset_config_cache
    from tests import legacy_text as lt

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "scope_gate": {"enabled": False},
                "installs": {
                    "issuesmith": "/var/tmp/issuesmith",
                    "ghdag": "/var/tmp/ghdag",
                    "mltgnt": "/var/tmp/mltgnt",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    table = (
        f"| {lt.REPOSITORY} | {lt.FILE_PATH} | {lt.CHANGE_TYPE} | {lt.DESCRIPTION} |\n"
        "|---|---|---|---|\n"
        "| `sumipan/nexus` | `pyproject.toml` | modify | raise ghdag pin |\n"
        "| `sumipan/nexus` | `tests/conventions/test_required_upstream_apis_exist.py` | modify | assert pin |\n"
    )
    recovery_body = f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - tests/conventions/test_required_upstream_apis_exist.py
```

## Design

Bump the ghdag pin to ghdag v0.104.5 in pyproject.toml.

### Change targets

{table}"""
    consistent_body = recovery_body.replace(
        "allow_paths:\n  - tests/conventions/test_required_upstream_apis_exist.py",
        "allow_paths:\n  - pyproject.toml\n  - tests/conventions/test_required_upstream_apis_exist.py",
    )
    try:
        recovery_v = collect_violations(recovery_body, [])
        consistent_v = collect_violations(consistent_body, [])
    finally:
        reset_config_cache()
    assert any(
        v.rule_id == "change_table_scope.outside_allow_paths"
        and v.location == "pyproject.toml"
        for v in recovery_v
    )
    assert not any(
        v.rule_id == "change_table_scope.outside_allow_paths" for v in consistent_v
    )
    assert not any(v.rule_id == "pin_bump.in_feature_issue" for v in consistent_v)


def test_allow_paths_forbidden_gate_is_registered():
    assert "allow_paths_forbidden" in DESIGN_DOC_GATES






def test_promoted_milestone_without_sub_blocks_fails_verify():
    """Regression (nexus #4002): 3 plan rows vs 0 sub blocks must not reach draft-done."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, ["scope:milestone"])}
    assert "milestone_subdesign.sub_count_mismatch" in rule_ids


def test_non_milestone_verify_shows_label_missing_and_subdesign_together():
    """R1: split-plan body without scope:milestone label shows both label_missing and sub_count_mismatch."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, [])}
    assert "milestone_consistency.label_missing" in rule_ids, rule_ids
    assert "milestone_subdesign.sub_count_mismatch" in rule_ids, rule_ids


def test_no_split_plan_body_ignores_milestone_subdesign():
    """R1: body without a split-plan pattern does not produce milestone violations without a label."""
    rule_ids = {v.rule_id for v in collect_violations(_VALID_BODY, [])}
    milestone_ids = {r for r in rule_ids if r.startswith(("milestone_subdesign.", "design_lint.milestone."))}
    assert not milestone_ids, milestone_ids


def test_assumed_labels_adds_scope_milestone_when_plan_present():
    """assumed_labels: split-plan body with no label → returns list with scope:milestone appended."""
    from issuesmith.step_verify import assumed_labels

    result = assumed_labels(_PROMOTED_MILESTONE_BODY, [])
    assert result == ["scope:milestone"]


def test_assumed_labels_noop_when_label_already_present():
    """assumed_labels: already has scope:milestone → returns copy unchanged."""
    from issuesmith.step_verify import assumed_labels

    result = assumed_labels(_PROMOTED_MILESTONE_BODY, ["scope:milestone"])
    assert result == ["scope:milestone"]


def test_assumed_labels_noop_when_no_split_plan():
    """assumed_labels: no split-plan pattern → returns copy of labels unchanged."""
    from issuesmith.step_verify import assumed_labels

    result = assumed_labels(_VALID_BODY, ["scope:feature"])
    assert result == ["scope:feature"]




def test_cp1_intentional_hold_excluded_with_assumed_labels():
    """R1: design_lint.intentional_hold must not appear even when scope:milestone is assumed."""
    rule_ids = {v.rule_id for v in collect_violations(_PROMOTED_MILESTONE_BODY, [])}
    assert "design_lint.intentional_hold" not in rule_ids


def test_complete_milestone_body_passes_milestone_subdesign():
    from tests.gate_rules.test_milestone_subdesign import _valid_body

    rule_ids = {v.rule_id for v in collect_violations(_valid_body(), ["scope:milestone"])}
    assert not {r for r in rule_ids if r.startswith("milestone_subdesign.")}, rule_ids


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
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "scope_gate": {"enabled": False},
                "steps": {"b1": {"verify": "design_doc"}},
            }
        ),
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




def test_deterministic_recovery_skips_normal_sized_issue(tmp_path, monkeypatch):
    """AC (#4191): scope_size-ineligible Issues are unchanged by promotion."""
    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.step_verify import apply_deterministic_recovery

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






def _setup_contract_repo(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    test_file = repo / "tests/skills/project_summary/test_fetch.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fetch():\n    pass\n", encoding="utf-8")
    skill = repo / "skills/project-summary/SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text("# skill\n", encoding="utf-8")
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


def _paths_must_not_exist_body(*, change_row: str, must_not_exist: list[str]) -> str:
    mne = "\n".join(f"  - {p}" for p in must_not_exist)
    return (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - skills/project-summary/**\n"
        "```\n\n"
        "## Changed Files\n\n"
        "| Repo | File | Change | Note |\n"
        "|---|---|---|---|\n"
        f"{change_row}\n\n"
        "## Acceptance Criteria\n\n"
        "```yaml\n"
        "paths_must_exist: []\n"
        f"paths_must_not_exist:\n{mne}\n"
        "references_must_resolve: []\n"
        "```\n"
    )




def test_paths_must_not_exist_allows_deletion_row(tmp_path, monkeypatch):
    import unittest.mock as mock

    import yaml

    from issuesmith.config import reset_config_cache

    repo = _setup_contract_repo(tmp_path)
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    path = "tests/skills/project_summary/test_fetch.py"
    body = _paths_must_not_exist_body(
        change_row=f"| sumipan/issuesmith | `{path}` | delete | rm tests |",
        must_not_exist=[path],
    )
    try:
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = collect_violations(body, [])
    finally:
        reset_config_cache()
    assert "scope_coupling.paths_must_not_exist_unjustified" not in {
        v.rule_id for v in violations
    }


def test_paths_must_not_exist_allows_missing_on_base(tmp_path, monkeypatch):
    import unittest.mock as mock

    import yaml

    from issuesmith.config import reset_config_cache

    repo = _setup_contract_repo(tmp_path)
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    body = _paths_must_not_exist_body(
        change_row="| sumipan/issuesmith | `skills/project-summary/SKILL.md` | delete | rm |",
        must_not_exist=["tests/skills/project_summary/test_missing.py"],
    )
    try:
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = collect_violations(body, [])
    finally:
        reset_config_cache()
    assert "scope_coupling.paths_must_not_exist_unjustified" not in {
        v.rule_id for v in violations
    }


def test_check_paths_must_not_exist_validity_wrapper(tmp_path, monkeypatch):
    import unittest.mock as mock

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.step_verify import check_paths_must_not_exist_validity

    repo = _setup_contract_repo(tmp_path)
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    body = _paths_must_not_exist_body(
        change_row="| sumipan/issuesmith | `skills/project-summary/SKILL.md` | delete | rm |",
        must_not_exist=["tests/skills/project_summary/test_fetch.py"],
    )
    try:
        with mock.patch(
            "issuesmith.scope_gate.resolve_scope_root",
            return_value=repo,
        ):
            violations = check_paths_must_not_exist_validity(body)
    finally:
        reset_config_cache()
    assert any(
        v.rule_id == "scope_coupling.paths_must_not_exist_unjustified" for v in violations
    )


def _recover(tmp_path, monkeypatch, body: str, runs: int = 1):
    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.step_verify import apply_deterministic_recovery

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    client = _FakeForge(body=body, labels=["scope:milestone"])
    results = []
    try:
        for _ in range(runs):
            results.append(apply_deterministic_recovery(
                client, 4825, client.body, ["scope:milestone"], persist=True
            ))
    finally:
        reset_config_cache()
    return client, results






def test_deterministic_recovery_applies_sub_plan_dep_format(tmp_path, monkeypatch):
    from issuesmith.gate_rules.milestone_subdesign_rules import _SUB_PLAN_DEP_FORMAT_ID
    from tests.gate_rules.test_milestone_subdesign import _dep_body, _projection_subs

    body = _dep_body(_projection_subs(), ["4", "2"])
    client, (result,) = _recover(tmp_path, monkeypatch, body)

    assert _SUB_PLAN_DEP_FORMAT_ID in result.applied
    assert "| 1 | part 1 | sumipan/issuesmith | c | #4 |" in result.body
    assert "#2" in result.body.split("| 2 | part 2 | sumipan/issuesmith | c | ", 1)[1]


