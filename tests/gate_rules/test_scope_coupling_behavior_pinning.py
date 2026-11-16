"""scope_coupling: behavior pinning and deletion-key stem boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from issuesmith.gate_rules import scope_coupling
from issuesmith.gate_rules.scope_coupling import (
    BEHAVIOR_PIN_RULE_ID,
    PATH_STRING_RULE_ID,
    check_allow_paths_string_references,
    check_behavior_pinning,
)
from tests.gate_rules import test_scope_coupling as _base
from tests.gate_rules.test_scope_coupling import _body, _git, _init_repo, _write_language_pack

_clear_config_cache = _base._clear_config_cache


# ---------------------------------------------------------------------------
# check_behavior_pinning and check_allow_paths_string_references (nexus #4134)
# ---------------------------------------------------------------------------


@pytest.fixture()
def pin_repo(tmp_path: Path) -> Path:
    """Git repo with test files referencing specific symbols and paths."""
    root = tmp_path / "pin_repo"
    files = {
        "src/memory.py": "def read_memory_iterative(): pass\n",
        "tests/test_memory.py": (
            "import memory\n"
            "def test_read_memory_iterative():\n"
            "    memory.read_memory_iterative()\n"
        ),
        "tests/test_mode.py": (
            'def test_mode():\n'
            '    assert config["mode"] == "iterative"\n'
        ),
        "tests/test_unrelated.py": "def test_pass(): pass\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root



def test_behavior_pinning_key_value_outside_allow_paths(pin_repo, tmp_path, monkeypatch):
    """`mode: iterative` removal; test with `iterative` outside allow_paths triggers violation."""
    _write_language_pack(tmp_path, monkeypatch, removal_words=["purge", "retire"])
    body_text = "## Changes\n\n`mode: iterative` is retired.\n"
    allow = ["src/memory.py"]
    violations = check_behavior_pinning(body_text, allow, pin_repo)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == BEHAVIOR_PIN_RULE_ID
    assert "tests/test_mode.py" in v.message


def test_behavior_pinning_covered_test_no_violation(pin_repo):
    """Test already in allow_paths; no violation."""
    body_text = "## Changes\n\nDelete `read_memory_iterative`.\n"
    allow = ["src/memory.py", "tests/test_memory.py"]
    assert check_behavior_pinning(body_text, allow, pin_repo) == []


def test_behavior_pinning_no_replacement_keywords_returns_empty(pin_repo):
    """No deletion/replacement keywords in body; no violation."""
    body_text = "## Changes\n\nThis is a new feature. Add `read_memory_iterative` here.\n"
    assert check_behavior_pinning(body_text, [], pin_repo) == []


def test_behavior_pinning_no_symbols_in_body_returns_empty(pin_repo):
    """Deletion keyword present but no backtick-quoted symbols; no violation."""
    body_text = "## Changes\n\nDelete old functionality.\n"
    assert check_behavior_pinning(body_text, [], pin_repo) == []


def test_allow_paths_string_reference_outside_allow_paths(tmp_path):
    """Test outside allow_paths references an allow_paths path string → violation."""
    root = tmp_path / "path_repo"
    skill_path = "skills/project-todo/SKILL.md"
    files = {
        "skills/project-todo/SKILL.md": "# SKILL\n",
        "tests/test_skill_path.py": f'PATH = "{skill_path}"\n',
        "tests/test_unrelated.py": "def test_pass(): pass\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    allow = [skill_path, "src/other.py"]
    violations = check_allow_paths_string_references(allow, root)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == PATH_STRING_RULE_ID
    assert v.severity == "fail"
    assert v.location == skill_path
    assert "tests/test_skill_path.py" in v.message
    assert "tests/test_skill_path.py" in (v.fix_hint or "")



# ---------------------------------------------------------------------------
# deletion_search_keys: module-path / slash-path keys and stem uniqueness (#4165)
# ---------------------------------------------------------------------------









def test_memory_dream_stem_boundary(tmp_path):
    root = tmp_path / "dream_py"
    files = {
        "scripts/memory-dream.py": "# module\n",
        "tools/fail.py": '"memory_dream_2026-09-27.failed"\n',
        "tools/import.py": "import memory_dream\n",
    }
    _init_repo(root, files)
    body = _body(
        ["scripts/memory-dream.py"],
        rows="| sumipan/issuesmith | scripts/memory-dream.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["scripts/memory-dream.py"], root
    )
    referrers = refs.get("scripts/memory-dream.py", [])
    assert "tools/fail.py" not in referrers
    assert "tools/import.py" not in referrers





# ---------------------------------------------------------------------------
# stem context matching (#4268)
# ---------------------------------------------------------------------------


@pytest.fixture()
def post_repo(tmp_path: Path) -> Path:
    root = tmp_path / "post_repo"
    files = {
        "skills/a/scripts/post.py": "# deleted target\n",
        "skills/a/scripts/runner.py": "import post\n",
        "skills/a/scripts/relative.py": "from . import post\n",
        "tools/import_pkg.py": "from skills.a.scripts import post\n",
        "tools/import_other.py": "from tools.slack.batch_notify import post\n",
        "tools/import_cross.py": "import post\n",
        "tools/dynamic.py": (
            'loader = spec_from_file_location("post", root / "post.py")\n'
        ),
        "tools/false_app.py": '@app.post("/sessions", response_model=SessionOut)\n',
        "tools/false_help.py": 'help="Do not post to Slack"\n',
        "tools/false_doc.py": '"""Load and post results."""\n',
    }
    return _init_repo(root, files)



def test_stem_post_true_positives_included(post_repo):
    body = _body(
        ["skills/a/scripts/post.py"],
        rows="| sumipan/issuesmith | skills/a/scripts/post.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["skills/a/scripts/post.py"], post_repo
    )
    referrers = refs.get("skills/a/scripts/post.py", [])
    for path in (
        "skills/a/scripts/runner.py",
        "skills/a/scripts/relative.py",
        "tools/import_pkg.py",
        "tools/dynamic.py",
    ):
        assert path in referrers


@pytest.fixture()
def memory_compact_repo(tmp_path: Path) -> Path:
    root = tmp_path / "memory_compact_repo"
    files = {
        "scripts/memory-compact.py": "# deleted target\n",
        "tools/comment.py": "# (later replaced with `memory_compact`)\n",
        "tools/docstring.py": '"""Priority for memory-compact reduction."""\n',
        "tools/yaml_ref.py": "path: scripts/memory-compact.py\n",
        "tools/basename.py": 'MODULE = "memory-compact.py"\n',
    }
    return _init_repo(root, files)





def test_behavior_pinning_ignores_verbs_outside_pack(pin_repo, tmp_path, monkeypatch):
    """EN verbs are not removal context when the pack does not list them."""
    _write_language_pack(tmp_path, monkeypatch, removal_words=["purge"])
    body_text = "## Changes\n\n`read_memory_iterative` is deleted.\n"
    assert check_behavior_pinning(body_text, ["src/memory.py"], pin_repo) == []
