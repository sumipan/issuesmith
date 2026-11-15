"""ScopeCouplingRules against real git repositories and removal names."""

from __future__ import annotations

import unittest.mock as mock
from pathlib import Path

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules.scope_coupling import ScopeCouplingRules
from tests import test_scope_coupling as _base

_clear_config_cache = _base._clear_config_cache


# ---------------------------------------------------------------------------
# AC-1 (search_dirs) and AC-1b: real git fixture
# ---------------------------------------------------------------------------

def _setup_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with files for search_dirs tests."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    def write(rel_path: str, content: str) -> None:
        p = repo / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    write("workflows/x-step.md", "# placeholder\n")
    write("tests/test_x.py", "STEP = 'x-step'\n")
    write("workflows/y.md", "x-step is used here\n")
    write("tools/z.py", "STEP = 'x-step'\n")

    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


_SEARCH_DIRS_BODY_TEMPLATE = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - workflows/x-step.md\n"
    "```\n\n"
    "## Changed Files\n\n"
    "| Repo | File | Change |\n"
    "| sumipan/issuesmith | workflows/x-step.md | delete |\n"
)


# ---------------------------------------------------------------------------
# AC-4: removal names in tables and headings (real git fixture)
# ---------------------------------------------------------------------------

_REMOVAL_TABLE_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/pkg/main.py\n"
    "```\n\n"
    "## Changed Files\n\n"
    "| Repo | File | Change |\n"
    "| sumipan/issuesmith | src/pkg/main.py | update |\n\n"
    "## Removal Details\n\n"
    "The following are being removed:\n\n"
    "| Symbol | Change |\n"
    "| `FOO_BAR` | remove |\n"
    "| `foo_bar` | remove |\n"
)

_REMOVAL_HEADING_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/pkg/main.py\n"
    "```\n\n"
    "## Items to remove: old_result\n\n"
    "The `${old_result}` template variable is no longer used.\n"
)

_REMOVAL_OUTSIDE_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    "  - src/pkg/main.py\n"
    "```\n\n"
    "## Normal Section\n\n"
    "The `BAZ_QUUX` identifier is mentioned here (no removal context).\n"
)


def _setup_removal_git_repo(tmp_path: Path) -> Path:
    """Create a git repo with files containing FOO_BAR, foo_bar, old_result, BAZ_QUUX."""
    import subprocess
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )

    def write(rel_path: str, content: str) -> None:
        p = repo / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    write("src/pkg/main.py", "FOO_BAR = 1\nfoo_bar = 2\nBAZ_QUUX = 3\n")
    write("tests/test_foo.py", "from src.pkg.main import FOO_BAR\n")
    write("src/pkg/bar.py", "from src.pkg.main import foo_bar\n")
    write("src/pkg/tmpl.py", "x = old_result\n")
    write("src/pkg/baz.py", "y = BAZ_QUUX\n")

    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


class TestRemovalNames:
    """AC-4, AC-4b, AC-4c: removal names extracted from tables and headings."""

    def test_removal_table_required_catches_callers(self, tmp_path, monkeypatch):
        """AC-4: FOO_BAR and foo_bar in removal table -> tests/test_foo.py and src/pkg/bar.py."""
        import yaml as _yaml

        repo = _setup_removal_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {"enabled": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_REMOVAL_TABLE_BODY, [])

        test_v = [v for v in violations if v.rule_id == "scope_coupling.tests_outside_allow_paths"]
        caller_v = [v for v in violations if v.rule_id == "scope_coupling.callers_outside_allow_paths"]
        assert test_v, f"Expected tests violation, got: {violations}"
        assert caller_v, f"Expected callers violation, got: {violations}"
        assert "tests/test_foo.py" in test_v[0].message
        assert "src/pkg/bar.py" in caller_v[0].message


    def test_outside_removal_context_not_required(self, tmp_path, monkeypatch):
        """AC-4c: BAZ_QUUX outside removal context is not required -> no violation."""
        import yaml as _yaml

        repo = _setup_removal_git_repo(tmp_path)
        cfg_path = tmp_path / "issuesmith.yaml"
        cfg_path.write_text(
            _yaml.safe_dump({
                "repo": "sumipan/issuesmith",
                "scope_coupling": {"enabled": True},
            }),
            encoding="utf-8",
        )
        monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
        reset_config_cache()

        with mock.patch(
            "issuesmith.gate_rules.scope_coupling.resolve_scope_root",
            return_value=repo,
        ):
            violations = ScopeCouplingRules().check(_REMOVAL_OUTSIDE_BODY, [])

        all_msgs = " ".join(v.message for v in violations)
        assert "src/pkg/baz.py" not in all_msgs, (
            f"BAZ_QUUX (no removal context) should not generate violation, got: {all_msgs}"
        )


class TestRemovalNamesUnit:
    """Unit tests for _removal_names on heading text itself."""

    def test_backtick_ident_in_removal_heading_extracted(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = "## Delete `MY_CONST`\n\nNo identifier in the body.\n"
        assert "MY_CONST" in _removal_names(body)

    def test_template_var_in_removal_heading_extracted(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = "### Remove `${old_step_result}`\n\nNo longer used.\n"
        assert "old_step_result" in _removal_names(body)

    def test_backtick_ident_in_non_removal_heading_ignored(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = "## About `KEEP_CONST`\n\nplain text\n"
        assert _removal_names(body) == set()

    def test_removal_section_closes_at_same_level_heading(self):
        from issuesmith.gate_rules.scope_coupling import _removal_names

        body = (
            "## Items to delete\n\n`GONE_NAME` goes away.\n\n"
            "## Next section\n\n`STAY_NAME` stays.\n"
        )
        assert _removal_names(body) == {"GONE_NAME"}
