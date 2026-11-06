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
    check_deletion_references,
    deletion_search_keys,
    uncovered_deletion_references,
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


def test_behavior_pinning_deletion_symbol_outside_allow_paths(pin_repo, tmp_path, monkeypatch):
    """Issue body deletes `read_memory_iterative` with a pack removal verb; uncovered test fails."""
    _write_language_pack(tmp_path, monkeypatch, removal_words=["purge", "retire"])
    body_text = "## Changes\n\n`read_memory_iterative` is purged.\n"
    allow = ["src/memory.py"]
    violations = check_behavior_pinning(body_text, allow, pin_repo)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == BEHAVIOR_PIN_RULE_ID
    assert v.severity == "fail"
    assert "tests/test_memory.py" in v.message
    assert "tests/test_memory.py" in (v.fix_hint or "")


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


def test_allow_paths_string_reference_covered_no_violation(tmp_path):
    """Test already in allow_paths; no violation for path string reference."""
    root = tmp_path / "path_repo2"
    skill_path = "skills/project-todo/SKILL.md"
    files = {
        "skills/project-todo/SKILL.md": "# SKILL\n",
        "tests/test_skill_path.py": f'PATH = "{skill_path}"\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    allow = [skill_path, "tests/test_skill_path.py"]
    assert check_allow_paths_string_references(allow, root) == []


# ---------------------------------------------------------------------------
# deletion_search_keys: module-path / slash-path keys and stem uniqueness (#4165)
# ---------------------------------------------------------------------------


def test_deletion_search_keys_no_repo_path_includes_module_and_slash():
    """Without repo_path, module-path and slash-path keys are included; bare stem is not."""
    keys = deletion_search_keys("tools/mltgnt_bridge/progress.py")
    assert "tools.mltgnt_bridge.progress" in keys
    assert "tools/mltgnt_bridge/progress.py" in keys
    # No bare stem without repo_path
    assert "progress" not in keys


def test_deletion_search_keys_with_repo_path_nonunique_stem_excluded(tmp_path):
    """When repo has multiple files with stem 'progress', bare stem is omitted."""
    root = tmp_path / "multirepo"
    for rel in (
        "tools/mltgnt_bridge/progress.py",
        "src/progress.py",  # second file with same stem
        "tests/test_prog.py",
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    keys = deletion_search_keys("tools/mltgnt_bridge/progress.py", root)
    assert "tools.mltgnt_bridge.progress" in keys
    assert "tools/mltgnt_bridge/progress.py" in keys
    assert "progress" not in keys, "non-unique stem must be excluded"


def test_deletion_search_keys_with_repo_path_unique_stem_included(tmp_path):
    """When stem is unique in the repo, bare stem is included as a compat key."""
    root = tmp_path / "uniqrepo"
    for rel in (
        "tools/mltgnt_bridge/progress.py",
        "tests/test_other.py",
    ):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# placeholder\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    keys = deletion_search_keys("tools/mltgnt_bridge/progress.py", root)
    assert "tools.mltgnt_bridge.progress" in keys
    assert "tools/mltgnt_bridge/progress.py" in keys
    assert "progress" in keys, "unique stem must be included as compat key"


def test_uncovered_deletion_references_no_false_positive_from_common_stem(tmp_path):
    """Unrelated files sharing a common stem are not reported as uncovered referrers."""
    root = tmp_path / "multirepo"
    files = {
        "tools/mltgnt_bridge/progress.py": "# progress tracker\n",
        "src/progress.py": "# unrelated progress module\n",
        "tests/test_other_progress.py": "import progress\n",  # unrelated hit
        "tests/test_bridge.py": "import tools.mltgnt_bridge.progress\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    body = _body(
        ["tools/mltgnt_bridge/progress.py"],
        rows="| sumipan/issuesmith | tools/mltgnt_bridge/progress.py | delete | retire |",
    )

    refs = uncovered_deletion_references(body, ["tools/mltgnt_bridge/progress.py"], root)
    # test_other_progress.py imports bare `progress` (shared stem) — must NOT be reported
    referrers = refs.get("tools/mltgnt_bridge/progress.py", [])
    assert "tests/test_other_progress.py" not in referrers
    # test_bridge.py uses the module path — must be reported
    assert "tests/test_bridge.py" in referrers


def test_nonunique_fetch_name_skips_partial_filename_hits(tmp_path):
    """Non-unique fetch.py: partial filename matches are ignored (#4245)."""
    root = tmp_path / "fetch_repo"
    files = {
        "skills/a/scripts/fetch.py": "# target\n",
        "other/skill/scripts/fetch.py": "# other fetch\n",
        "scripts/x-fetch.py": 'CMD = "x-fetch only"\n',
        "tools/ref.py": 'PATH = "skills/a/scripts/fetch.py"\n',
    }
    _init_repo(root, files)
    body = _body(
        ["skills/a/scripts/fetch.py"],
        rows="| sumipan/issuesmith | skills/a/scripts/fetch.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["skills/a/scripts/fetch.py"], root
    )
    referrers = refs.get("skills/a/scripts/fetch.py", [])
    assert "scripts/x-fetch.py" not in referrers
    assert "other/skill/scripts/fetch.py" not in referrers
    assert "tools/ref.py" in referrers


def test_unique_fetch_name_requires_filename_boundary(tmp_path):
    root = tmp_path / "solo_fetch"
    files = {
        "scripts/solo/fetch.py": "# solo\n",
        "scripts/x-fetch.py": 'CMD = "1on1-slack-fetch.py"\n',
        "tools/quote.py": 'NAME = "fetch.py"\n',
        "tools/path.py": 'PATH = "scripts/solo/fetch.py"\n',
    }
    _init_repo(root, files)
    body = _body(
        ["scripts/solo/fetch.py"],
        rows="| sumipan/issuesmith | scripts/solo/fetch.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["scripts/solo/fetch.py"], root
    )
    referrers = refs.get("scripts/solo/fetch.py", [])
    assert "scripts/x-fetch.py" not in referrers
    assert "tools/quote.py" in referrers
    assert "tools/path.py" in referrers


def test_unique_compaction_stem_requires_identifier_boundary(tmp_path):
    root = tmp_path / "compaction_repo"
    files = {
        "tools/secretary/_compaction.py": "# module\n",
        "tools/needs.py": "def needs_compaction():\n    pass\n",
        "tools/import_mod.py": "from tools.secretary import _compaction\n",
        "tools/import_dot.py": "tools.secretary._compaction\n",
        "tools/import_bare.py": "import _compaction\n",
    }
    _init_repo(root, files)
    body = _body(
        ["tools/secretary/_compaction.py"],
        rows="| sumipan/issuesmith | tools/secretary/_compaction.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["tools/secretary/_compaction.py"], root
    )
    referrers = refs.get("tools/secretary/_compaction.py", [])
    assert "tools/needs.py" not in referrers
    assert "tools/import_mod.py" in referrers
    assert "tools/import_dot.py" in referrers
    assert "tools/import_bare.py" not in referrers


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


def test_non_py_json_deletion_skips_prose_dream(tmp_path):
    root = tmp_path / "dream_json"
    files = {
        "chat/dream/dream.json": "{}\n",
        "scripts/prose.py": "text about a dream here\n",
        "scripts/mltgnt.py": "from mltgnt.memory.dream import x\n",
    }
    _init_repo(root, files)
    body = _body(
        ["chat/dream/dream.json"],
        rows="| sumipan/issuesmith | chat/dream/dream.json | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["chat/dream/dream.json"], root
    )
    assert refs == {}


def test_check_deletion_references_fix_hint_includes_kind_and_line(tmp_path):
    root = tmp_path / "hint_repo"
    files = {
        "scripts/solo/fetch.py": "# solo\n",
        "tools/path.py": 'PATH = "scripts/solo/fetch.py"\n',
    }
    _init_repo(root, files)
    body = _body(
        ["scripts/solo/fetch.py"],
        rows="| sumipan/issuesmith | scripts/solo/fetch.py | delete | retire |",
    )
    [v] = check_deletion_references(body, ["scripts/solo/fetch.py"], root)
    hint = v.fix_hint or ""
    assert "tools/path.py:" in hint
    assert "`fetch.py`" in hint or "`scripts/solo/fetch.py`" in hint
    assert "name `" in hint or "path `" in hint
    assert 'PATH = "scripts/solo/fetch.py"' in hint


def test_no_stem_search_from_allow_paths_path(tmp_path):
    """`foo.py` in allow_paths; test has only `foo` (not the full path) → no violation."""
    root = tmp_path / "stem_repo"
    files = {
        "src/foo.py": "x = 1\n",
        "tests/test_foo.py": 'x = "foo"\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    allow = ["src/foo.py"]
    # Searches for exact string "src/foo.py" in tests; "foo" alone does not match.
    assert check_allow_paths_string_references(allow, root) == []


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


def test_stem_post_false_positives_excluded(post_repo):
    body = _body(
        ["skills/a/scripts/post.py"],
        rows="| sumipan/issuesmith | skills/a/scripts/post.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["skills/a/scripts/post.py"], post_repo
    )
    referrers = refs.get("skills/a/scripts/post.py", [])
    for path in (
        "tools/false_app.py",
        "tools/false_help.py",
        "tools/false_doc.py",
        "tools/import_other.py",
        "tools/import_cross.py",
    ):
        assert path not in referrers


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


def test_stem_memory_compact_false_positives_excluded(memory_compact_repo):
    body = _body(
        ["scripts/memory-compact.py"],
        rows="| sumipan/issuesmith | scripts/memory-compact.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["scripts/memory-compact.py"], memory_compact_repo
    )
    referrers = refs.get("scripts/memory-compact.py", [])
    assert "tools/comment.py" not in referrers
    assert "tools/docstring.py" not in referrers


def test_stem_memory_compact_true_positives_included(memory_compact_repo):
    body = _body(
        ["scripts/memory-compact.py"],
        rows="| sumipan/issuesmith | scripts/memory-compact.py | delete | retire |",
    )
    refs = scope_coupling.uncovered_deletion_references(
        body, ["scripts/memory-compact.py"], memory_compact_repo
    )
    referrers = refs.get("scripts/memory-compact.py", [])
    assert "tools/yaml_ref.py" in referrers
    assert "tools/basename.py" in referrers


@pytest.mark.parametrize(
    ("overrides", "verb"),
    [({}, "deleted"), ({"removal_words": ["purge", "retire"]}, "purged")],
    ids=["en", "ascii_pack"],
)
def test_behavior_pinning_pack_verbs_match_en_decision(
    pin_repo, tmp_path, monkeypatch, overrides, verb
):
    """A removal verb of the configured pack gives the same violation as the EN verb."""
    _write_language_pack(tmp_path, monkeypatch, **overrides)
    body_text = f"## Changes\n\n`read_memory_iterative` is {verb}.\n"
    violations = check_behavior_pinning(body_text, ["src/memory.py"], pin_repo)
    assert [v.rule_id for v in violations] == [BEHAVIOR_PIN_RULE_ID]
    assert "tests/test_memory.py" in violations[0].message


def test_behavior_pinning_ignores_verbs_outside_pack(pin_repo, tmp_path, monkeypatch):
    """EN verbs are not removal context when the pack does not list them."""
    _write_language_pack(tmp_path, monkeypatch, removal_words=["purge"])
    body_text = "## Changes\n\n`read_memory_iterative` is deleted.\n"
    assert check_behavior_pinning(body_text, ["src/memory.py"], pin_repo) == []
