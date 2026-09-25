"""tests/test_cp1_scope_autofix.py — CP1 deterministic allow_paths auto-narrow (#3487 AC-2/AC-3).

Mirrors the nexus #3483 failure: a cross-repo child inherited an oversized
``tests/**`` allow_paths (85 files alone exceeds max_files=80), CP1 passed
silently, and P0 tripped instead. CP1 (gate_rules.scope_breadth) must now
narrow allow_paths to the union of the change table and the AC
``paths_must_exist`` list and continue, when that narrower set is itself
within threshold — never fail on scope the Issue's own design already commits
to touching.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.b1_verify import collect_violations
from issuesmith.config import ScopeCouplingConfig, ScopeGateConfig
from issuesmith.cp1_gate import check_gate
from tests.legacy_text import (
    ACCEPTANCE_CRITERIA,
    CHANGE_TYPE,
    CHANGED_FILES,
    DESCRIPTION,
    FILE_PATH,
    MODIFY,
    REPOSITORY,
)

_CHANGE_TABLE_HEADER = f"{REPOSITORY} | {FILE_PATH} | {CHANGE_TYPE} | {DESCRIPTION}"

_NARROW_PATHS = [
    "src/issuesmith/scope_widget.py",
    "src/issuesmith/scope_widget_helper.py",
    "tests/test_scope_widget.py",
]


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )


def _commit_all(repo: Path) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def _oversized_clone(tmp_path: Path) -> Path:
    """Fake ``issuesmith`` clone: 85 ``tests/*.py`` (exceeds max_files=80) plus
    the 3 files the change table / AC actually name."""
    repo = tmp_path / ".claude" / "external" / "issuesmith"
    _git_init(repo)
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    for i in range(85):
        (tests_dir / f"t{i:03d}.py").write_text(f"# {i}\n", encoding="utf-8")
    for narrow in _NARROW_PATHS:
        path = repo / narrow
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# narrow scope file\n", encoding="utf-8")
    _commit_all(repo)
    return repo


def _body_with_table_and_ac(allow_paths: list[str]) -> str:
    allow_lines = "\n".join(f'  - "{p}"' for p in allow_paths)
    table_rows = "\n".join(
        f"| `sumipan/issuesmith` | `{p}` | {MODIFY} | narrow it |" for p in _NARROW_PATHS
    )
    return (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f"{allow_lines}\n"
        "```\n\n"
        "## Overview\n\nNarrow a wide scope.\n\n"
        f"## {CHANGED_FILES}\n\n"
        f"| {_CHANGE_TABLE_HEADER} |\n"
        "|---|---|---|---|\n"
        f"{table_rows}\n\n"
        f"## {ACCEPTANCE_CRITERIA}\n\n"
        "```yaml\n"
        "paths_must_exist:\n"
        f'  - "{_NARROW_PATHS[0]}"\n'
        "```\n"
    )


def _cfg(tmp_path: Path) -> MagicMock:
    cfg = MagicMock()
    cfg.root = tmp_path / "nexus"
    cfg.paths.external_dir = tmp_path / ".claude" / "external"
    cfg.scope_gate = ScopeGateConfig()
    cfg.scope_coupling = ScopeCouplingConfig()
    return cfg


@contextmanager
def _patch_cfg(tmp_path: Path):
    """Point both fail-closed gates (scope_breadth, scope_coupling) at the tmp clone."""
    cfg = _cfg(tmp_path)
    with (
        patch("issuesmith.gate_rules.scope_breadth.get_config", return_value=cfg),
        patch("issuesmith.gate_rules.scope_coupling.get_config", return_value=cfg),
    ):
        yield cfg


def test_check_gate_autofixes_and_passes(tmp_path: Path) -> None:
    """AC-2: oversized tests/** narrows to the 3-file union, PASS, note returned."""
    _oversized_clone(tmp_path)
    body = _body_with_table_and_ac(["tests/**"])
    with _patch_cfg(tmp_path):
        result = check_gate(body, [])
    assert result["status"] == "PASS"
    assert result["reasons"] == []
    assert result["autofix_new_allow_paths"] == _NARROW_PATHS
    assert result["autofix_note"] is not None
    assert "tests/**" in result["autofix_note"]
    for path in _NARROW_PATHS:
        assert path in result["autofix_note"]


def test_check_gate_fails_when_no_table_or_ac_to_narrow_to(tmp_path: Path) -> None:
    """No change table / paths_must_exist to narrow to -> FAIL, never a silent pass."""
    _oversized_clone(tmp_path)
    body = (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tests/**"\n'
        "```\n\n"
        "## Overview\n\nNo change table here.\n"
    )
    with _patch_cfg(tmp_path):
        result = check_gate(body, [])
    assert result["status"] == "FAIL"
    assert any(
        "scope_breadth.too_large" in r or "scope too large" in r for r in result["reasons"]
    )
    assert result["autofix_new_allow_paths"] is None


def test_narrow_allow_paths_already_within_threshold_is_a_plain_pass(tmp_path: Path) -> None:
    """No autofix needed when allow_paths is already within threshold."""
    _oversized_clone(tmp_path)
    body = _body_with_table_and_ac(_NARROW_PATHS)
    with _patch_cfg(tmp_path):
        result = check_gate(body, [])
    assert result["status"] == "PASS"
    assert result["autofix_new_allow_paths"] is None
    assert result["autofix_note"] is None


def test_b1_verify_reaches_the_same_result(tmp_path: Path) -> None:
    """AC-3: b1_verify shares gate_rules.scope_breadth — no separate narrowing logic."""
    _oversized_clone(tmp_path)
    body = _body_with_table_and_ac(["tests/**"])
    with _patch_cfg(tmp_path):
        violations = collect_violations(body, [])
    assert violations == []


def test_main_persists_the_narrowed_allow_paths_and_comments(tmp_path: Path) -> None:
    """AC-2 end-to-end: cp1_gate.main() writes the narrowed body and posts the note."""
    import sys

    from issuesmith import cp1_gate

    _oversized_clone(tmp_path)
    body = _body_with_table_and_ac(["tests/**"])
    forge = MagicMock()
    forge.issue_get.return_value = {"body": body, "labels": []}
    with (
        _patch_cfg(tmp_path),
        patch.object(cp1_gate, "get_forge", return_value=forge),
        patch.object(sys, "argv", ["issuesmith.cp1_gate", "42"]),
    ):
        cp1_gate.main()

    forge.issue_update.assert_called_once()
    _, kwargs = forge.issue_update.call_args
    from issuesmith.context_hook import parse_issue_metadata

    new_metadata = parse_issue_metadata(kwargs["body"])
    assert new_metadata["allow_paths"] == _NARROW_PATHS
    forge.issue_comment.assert_called_once()
    assert "tests/**" in forge.issue_comment.call_args.args[1]


def test_main_does_not_touch_the_issue_when_no_autofix(tmp_path: Path) -> None:
    """A clean, already-narrow body triggers no forge writes at all."""
    import sys

    from issuesmith import cp1_gate

    _oversized_clone(tmp_path)
    body = _body_with_table_and_ac(_NARROW_PATHS)
    forge = MagicMock()
    forge.issue_get.return_value = {"body": body, "labels": []}
    with (
        _patch_cfg(tmp_path),
        patch.object(cp1_gate, "get_forge", return_value=forge),
        patch.object(sys, "argv", ["issuesmith.cp1_gate", "42"]),
    ):
        cp1_gate.main()

    forge.issue_update.assert_not_called()
    forge.issue_comment.assert_not_called()
