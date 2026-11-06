"""ScopeCouplingRules: data-file tests, sibling deletion references and language pack vocabulary."""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules.scope_coupling import ScopeCouplingRules
from tests import test_scope_coupling as _base
from tests.test_scope_coupling import _FAKE_ROOT

_clear_config_cache = _base._clear_config_cache


# ---------------------------------------------------------------------------
# nexus #3949 — data / config file modifications require tests that pin them
# ---------------------------------------------------------------------------

_DATA_FILE_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - configs/vcs.yml\n"
    "  - scripts/check-vcs-ownership.py\n"
    "```\n\n"
    "## Changed Files\n\n"
    "| Repo | File | Change |\n"
    "|---|---|---|\n"
    "| sumipan/nexus | configs/vcs.yml | {change} |\n"
    "| sumipan/nexus | scripts/check-vcs-ownership.py | modify |\n"
)


def _setup_vcs_repo(tmp_path: Path) -> Path:
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "test@test.com"), ("user.name", "Test")):
        subprocess.run(
            ["git", "-C", str(repo), "config", key, value], check=True, capture_output=True,
        )
    files = {
        "configs/vcs.yml": "rows: []\n",
        "scripts/check-vcs-ownership.py": "CONFIG = 'configs/vcs.yml'\n",
        "tests/scripts/test_check_vcs_ownership.py": (
            "CONFIG = ROOT / 'configs' / 'vcs.yml'\nEXPECTED = {}\n"
        ),
        "tests/test_unrelated.py": "X = 1\n",
    }
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True,
    )
    return repo


class TestDataFileTests:
    def test_modified_yml_basename_is_required(self):
        from issuesmith.context_hook import parse_issue_metadata
        from issuesmith.gate_rules.scope_coupling import _extract_search_keys

        body = _DATA_FILE_BODY.format(change="modify")
        with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", return_value=[]):
            required, optional = _extract_search_keys(body, parse_issue_metadata(body), _FAKE_ROOT)
        assert "vcs.yml" in required
        assert "vcs.yml" not in optional

    @pytest.mark.parametrize("ext", [".yaml", ".json", ".toml", ".txt"])
    def test_other_data_extensions_are_required(self, ext):
        from issuesmith.context_hook import parse_issue_metadata
        from issuesmith.gate_rules.scope_coupling import _extract_search_keys

        body = _DATA_FILE_BODY.format(change="modify").replace("vcs.yml", f"vcs{ext}")
        with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", return_value=[]):
            required, _ = _extract_search_keys(body, parse_issue_metadata(body), _FAKE_ROOT)
        assert f"vcs{ext}" in required

    def test_code_file_modification_is_not_required(self):
        from issuesmith.context_hook import parse_issue_metadata
        from issuesmith.gate_rules.scope_coupling import _extract_search_keys

        body = _DATA_FILE_BODY.format(change="modify")
        with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", return_value=[]):
            required, _ = _extract_search_keys(body, parse_issue_metadata(body), _FAKE_ROOT)
        assert "check-vcs-ownership.py" not in required
        assert "check-vcs-ownership" not in required

    def test_data_file_tests_false_skips(self):
        from issuesmith.context_hook import parse_issue_metadata
        from issuesmith.gate_rules.scope_coupling import _extract_search_keys

        body = _DATA_FILE_BODY.format(change="modify")
        with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", return_value=[]):
            required, _ = _extract_search_keys(
                body, parse_issue_metadata(body), _FAKE_ROOT, data_file_tests=False,
            )
        assert "vcs.yml" not in required

    def test_deleted_yml_keeps_stem_only(self):
        from issuesmith.context_hook import parse_issue_metadata
        from issuesmith.gate_rules.scope_coupling import _extract_search_keys

        body = _DATA_FILE_BODY.format(change="delete").replace("configs/vcs.yml", "configs/vcsmap.yml")
        with mock.patch("issuesmith.gate_rules.scope_coupling._git_grep", return_value=[]):
            required, _ = _extract_search_keys(body, parse_issue_metadata(body), _FAKE_ROOT)
        assert "vcsmap" in required
        assert "vcsmap.yml" not in required

    def test_check_adds_pinning_test_to_allow_paths(self, tmp_path, monkeypatch):
        repo = _setup_vcs_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            yaml.safe_dump({"repo": "sumipan/nexus", "scope_coupling": {"search_dirs": ["tests"]}}),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        rule = ScopeCouplingRules()
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root", return_value=repo,
        ):
            violations = rule.check(_DATA_FILE_BODY.format(change="modify"), [])

        tests_v = [v for v in violations if v.rule_id == "scope_coupling.tests_outside_allow_paths"]
        assert len(tests_v) == 1
        assert "tests/scripts/test_check_vcs_ownership.py" in tests_v[0].message
        assert "tests/test_unrelated.py" not in tests_v[0].message
        assert tests_v[0].auto_fixable is True
        assert rule.autofix_new_allow_paths is not None
        assert "tests/scripts/test_check_vcs_ownership.py" in rule.autofix_new_allow_paths

    def test_check_honours_config_false(self, tmp_path, monkeypatch):
        repo = _setup_vcs_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            yaml.safe_dump({
                "repo": "sumipan/nexus",
                "scope_coupling": {"search_dirs": ["tests"], "data_file_tests": False},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root", return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_DATA_FILE_BODY.format(change="modify"), [])
        assert [v for v in violations if v.rule_id == "scope_coupling.tests_outside_allow_paths"] == []

    def test_disabled_gate_skips_data_file_tests(self, tmp_path, monkeypatch):
        repo = _setup_vcs_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            yaml.safe_dump({
                "repo": "sumipan/nexus",
                "scope_coupling": {"enabled": False, "data_file_tests": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root", return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_DATA_FILE_BODY.format(change="modify"), [])
        assert violations == []


# ---------------------------------------------------------------------------
# #4257 — sibling sub coverage for deletion references
# ---------------------------------------------------------------------------

_DELETED_SKILL_PATH = "skills/project_summary/fetch.py"

# Vocabulary of an ASCII language pack that differs from the EN defaults (#4474).
_SUB_MARKER = "Part"
_PARENT_ISSUE_HEADING = "Parent ticket"
_DESIGN_HEADING = "Design"
_CHANGED_FILES_HEADING = "Changed Files"
_FROM_DERIVED = "spun off"


@pytest.fixture()
def sibling_language_pack(tmp_path, monkeypatch):
    """Config whose language pack uses the vocabulary above."""
    import dataclasses

    from issuesmith.config import load_config
    from issuesmith.language import EN

    data = {f.name: getattr(EN, f.name) for f in dataclasses.fields(EN)}
    data.update(
        sub_header_prefix=_SUB_MARKER,
        parent_issue_label=_PARENT_ISSUE_HEADING,
        derived_from_phrase=_FROM_DERIVED,
    )
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
    return load_config()


_MILESTONE_PARENT_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - skills/project_summary/**\n"
    "```\n\n"
    "## Design\n\n"
    f"#### {_SUB_MARKER}2: delete skill module\n\n"
    "**Changed Files**:\n"
    "| Repo | File | Change | Note |\n"
    "|---|---|---|---|\n"
    f"| sumipan/issuesmith | `{_DELETED_SKILL_PATH}` | delete | rm module |\n\n"
    f"#### {_SUB_MARKER}3: update tests\n\n"
    "**Changed Files**:\n"
    "| Repo | File | Change | Note |\n"
    "|---|---|---|---|\n"
    "| sumipan/issuesmith | `tests/skills/project_summary/test_fetch.py` | modify | update |\n"
    "| sumipan/issuesmith | `tests/skills/project_summary/test_skill_md.py` | modify | update |\n"
)


def _setup_deletion_repo(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    files = {
        _DELETED_SKILL_PATH: "def fetch():\n    return 1\n",
        "tests/skills/project_summary/test_fetch.py": (
            'from skills.project_summary.fetch import fetch\n'
        ),
        "tests/skills/project_summary/test_skill_md.py": (
            "import skills.project_summary.fetch\n"
        ),
        "tests/other/test_uncovered.py": (
            "from skills.project_summary.fetch import fetch\n"
        ),
    }
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
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


def _sub_issue_body(sub_num: int, parent_num: int = 4000) -> str:
    return (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - skills/project_summary/**\n"
        "```\n\n"
        f"{_PARENT_ISSUE_HEADING}: #{parent_num}\n"
        f"## {_DESIGN_HEADING}\n\n"
        f"> {_PARENT_ISSUE_HEADING} #{parent_num} {_SUB_MARKER}{sub_num} {_FROM_DERIVED}\n\n"
        f"## {_CHANGED_FILES_HEADING}\n\n"
        "| Repo | File | Change | Note |\n"
        "|---|---|---|---|\n"
        f"| sumipan/issuesmith | `{_DELETED_SKILL_PATH}` | delete | rm module |\n"
    )


@pytest.mark.usefixtures("sibling_language_pack")
class TestSiblingDeletionReferences:
    def test_sibling_owned_referrer_is_not_uncovered(self, tmp_path):
        from issuesmith.gate_rules.scope_coupling import check_deletion_references

        repo = _setup_deletion_repo(tmp_path)
        body = _sub_issue_body(2)
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling._body_with_sub_blocks",
            return_value=_MILESTONE_PARENT_BODY,
        ):
            with mock.patch(
                "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
                return_value=repo,
            ):
                violations = check_deletion_references(
                    body,
                    [_DELETED_SKILL_PATH],
                    repo,
                )
        uncovered = {
            ref
            for v in violations
            for ref in (v.message or "").split(": ", 1)[-1].split(", ")
        }
        assert "tests/skills/project_summary/test_fetch.py" not in uncovered
        assert "tests/skills/project_summary/test_skill_md.py" not in uncovered
        assert any(
            "tests/other/test_uncovered.py" in v.message for v in violations
        )

    def test_uncovered_referrer_still_reported(self, tmp_path):
        from issuesmith.gate_rules.scope_coupling import check_deletion_references

        repo = _setup_deletion_repo(tmp_path)
        body = _sub_issue_body(2)
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling._body_with_sub_blocks",
            return_value=_MILESTONE_PARENT_BODY,
        ):
            violations = check_deletion_references(
                body,
                [_DELETED_SKILL_PATH],
                repo,
            )
        assert violations
        assert any("tests/other/test_uncovered.py" in v.message for v in violations)

    def test_sibling_note_mentions_sub_number(self, tmp_path):
        from issuesmith.gate_rules.scope_coupling import check_deletion_references

        repo = _setup_deletion_repo(tmp_path)
        body = _sub_issue_body(2)
        with mock.patch(
            "issuesmith.gate_rules.scope_coupling._body_with_sub_blocks",
            return_value=_MILESTONE_PARENT_BODY,
        ):
            violations = check_deletion_references(
                body,
                [_DELETED_SKILL_PATH],
                repo,
            )
        combined = "\n".join((v.message or "") + (v.fix_hint or "") for v in violations)
        assert "sibling sub #3" in combined.lower()


@pytest.mark.usefixtures("sibling_language_pack")
class TestChildBodyVocabulary:
    """The child-body sub number and parent number are read with the pack vocabulary."""

    def test_current_sub_number_uses_pack_phrase(self):
        from issuesmith.gate_rules.scope_coupling import _current_sub_number

        assert _current_sub_number(_sub_issue_body(3)) == 3

    def test_current_sub_number_ignores_other_vocabulary(self):
        from issuesmith.gate_rules.scope_coupling import _current_sub_number

        assert _current_sub_number("> Parent issue #4000 Sub3 derived\n") is None

    def test_parent_number_uses_pack_label(self):
        from issuesmith.gate_rules.scope_coupling import _parent_number_re

        match = _parent_number_re().search(_sub_issue_body(2, parent_num=4321))
        assert match is not None
        assert match.group(1) == "4321"

    def test_body_with_sub_blocks_fetches_parent_by_pack_label(self):
        from issuesmith.gate_rules.scope_coupling import _body_with_sub_blocks

        forge = mock.Mock()
        forge.issue_get.return_value = {"body": _MILESTONE_PARENT_BODY}
        with mock.patch("ghdag.forge.get_forge", return_value=forge):
            parent = _body_with_sub_blocks(_sub_issue_body(2, parent_num=4321))
        forge.issue_get.assert_called_once_with(4321, fields=["body"])
        assert parent == _MILESTONE_PARENT_BODY
